"""Celery tasks for orchestrating the alert pipeline.

This module implements the "Service Orchestrator": a linear Celery signature chain
that turns an incoming trigger into a background workflow.

Flow:
1) alerts: ingest webhook payload → create/update Alert + Incident
2) checkers: run diagnostics associated with the incident
3) intelligence: analyze incident and produce recommendations
4) notify: send final analysis to configured channels

The chain uses Celery Signatures so retries can happen per-stage.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Protocol, cast

from celery import chain, shared_task


def _ensure_jsonable(value: Any) -> Any:
    """Best-effort conversion to JSON-serializable types."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(cast(Any, value))
    return value


def build_orchestration_chain(ctx: dict[str, Any]):
    """Build the Celery signature chain for the orchestration pipeline."""
    return chain(
        alerts_ingest.s(ctx),
        run_diagnostics.s(),
        analyze_incident.s(),
        notify_channels.s(),
    )


@shared_task(bind=True)
def orchestrate_event(self, ctx: dict[str, Any]) -> dict[str, Any]:
    """Entry-point task that dispatches the per-stage chain.

    Returns a small payload with the chain result id so HTTP handlers can return quickly.
    """
    res = build_orchestration_chain(ctx).apply_async()
    return {"status": "queued", "pipeline_id": res.id}


@shared_task
def alerts_ingest(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 1: ingest the initial trigger and create/update incident state."""
    trigger = ctx.get("trigger", "webhook")

    if trigger != "webhook":
        # For now, the orchestrator is webhook-first. Keep stage deterministic.
        ctx.setdefault("errors", []).append(f"Unsupported trigger: {trigger}")
        return ctx

    payload = ctx.get("payload")
    driver = ctx.get("driver")

    if not isinstance(payload, dict):
        ctx.setdefault("errors", []).append("payload must be a JSON object")
        return ctx

    from apps.alerts.models import Alert
    from apps.alerts.services import AlertOrchestrator

    orchestrator = AlertOrchestrator()
    result = orchestrator.process_webhook(payload, driver=driver)

    ctx["alerts"] = {
        "created": result.alerts_created,
        "updated": result.alerts_updated,
        "resolved": result.alerts_resolved,
        "incidents_created": result.incidents_created,
        "incidents_updated": result.incidents_updated,
        "errors": list(result.errors),
    }

    # Best-effort selection of the most recent incident touched by this trigger.
    incident_id: int | None = None
    latest_alert = Alert.objects.order_by("-received_at").select_related("incident").first()
    if latest_alert and latest_alert.incident_id:
        incident_id = latest_alert.incident_id

    if incident_id:
        ctx["incident_id"] = incident_id

    if result.has_errors:
        ctx.setdefault("errors", []).extend(result.errors)

    return ctx


@shared_task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3
)
def run_diagnostics(self, ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 2: run server diagnostics for the incident.

    In this starter implementation, we run all checks via CheckAlertBridge.
    This already has solid logic for executing checkers and creating alerts.
    """
    from apps.alerts.check_integration import CheckAlertBridge

    bridge = CheckAlertBridge()
    check_names = ctx.get("checker_names")
    checker_configs = ctx.get("checker_configs")
    labels = ctx.get("labels")

    result = bridge.run_checks_and_alert(
        checker_names=check_names,
        checker_configs=checker_configs,
        labels=labels,
    )

    ctx["checkers"] = {
        "checks_run": result.checks_run,
        "alerts_created": result.alerts_created,
        "alerts_updated": result.alerts_updated,
        "alerts_resolved": result.alerts_resolved,
        "incidents_created": result.incidents_created,
        "incidents_updated": result.incidents_updated,
        "errors": list(result.errors),
    }

    if result.has_errors:
        ctx.setdefault("errors", []).extend(result.errors)

    return ctx


@shared_task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3
)
def analyze_incident(self, ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 3: generate intelligence recommendations.

    Uses apps.intelligence providers. This is the step where transient LLM/API
    failures are expected; Celery retries isolate the failure to this stage.
    """
    provider_name = ctx.get("provider", "local")
    provider_config = ctx.get("provider_config", {})

    from apps.intelligence.providers import get_provider

    provider = get_provider(provider_name, **(provider_config or {}))

    incident_id = ctx.get("incident_id")
    recommendations = None

    if incident_id:
        from apps.alerts.models import Incident

        incident = Incident.objects.filter(id=incident_id).first()
        recommendations = provider.analyze(incident)
    else:
        recommendations = provider.get_recommendations()

    ctx["intelligence"] = {
        "provider": provider_name,
        "incident_id": incident_id,
        "recommendations": [_ensure_jsonable(r.to_dict()) for r in recommendations],
        "count": len(recommendations),
    }

    return ctx


@shared_task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3
)
def notify_channels(self, ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 4: send the final message via notify drivers."""

    # Keep notify configurable but safe by default.
    driver_name = ctx.get("notify_driver", "generic")
    config = ctx.get("notify_config", {})
    channel = ctx.get("notify_channel", "default")

    from apps.notify.drivers.base import NotificationMessage
    from apps.notify.views import DRIVER_REGISTRY

    class _NotifyDriver(Protocol):
        def validate_config(self, config: dict[str, Any]) -> bool: ...

        def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]: ...

    driver_cls = cast(type[_NotifyDriver] | None, DRIVER_REGISTRY.get(driver_name))

    title = "Incident analysis"
    message_body = "No recommendations available."
    severity = "info"

    intelligence = ctx.get("intelligence") or {}
    recs = intelligence.get("recommendations") or []

    if recs:
        title = recs[0].get("title") or title
        # Simple text rendering for now; drivers can render richer formats.
        lines: list[str] = []
        for r in recs[:10]:
            prio = r.get("priority", "")
            r_title = r.get("title", "")
            desc = r.get("description", "")
            lines.append(f"- [{prio}] {r_title}: {desc}")
        message_body = "\n".join(lines)

        # Map priority → severity roughly.
        max_prio = {"critical": 3, "high": 2, "medium": 1, "low": 0}
        weight = -1
        for r in recs:
            weight = max(weight, max_prio.get((r.get("priority") or "").lower(), 0))
        severity = "critical" if weight >= 3 else "warning" if weight >= 2 else "info"

    if driver_cls is None:
        ctx.setdefault("errors", []).append(
            f"Unknown notify driver: {driver_name}. Available: {list(DRIVER_REGISTRY.keys())}"
        )
        return ctx

    driver = driver_cls()
    if not driver.validate_config(config):
        ctx.setdefault("errors", []).append(
            f"Invalid configuration for notify driver: {driver_name}"
        )
        return ctx

    msg = NotificationMessage(
        title=title,
        message=message_body,
        severity=severity,
        channel=channel,
        tags=ctx.get("tags") or {},
        context={
            "incident_id": ctx.get("incident_id"),
            "alerts": ctx.get("alerts"),
            "checkers": ctx.get("checkers"),
            "intelligence": ctx.get("intelligence"),
        },
    )

    result = driver.send(msg, config)
    ctx["notify"] = {"driver": driver_name, "result": _ensure_jsonable(result)}

    return ctx
