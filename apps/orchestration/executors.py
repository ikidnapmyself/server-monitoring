"""
Stage executors for each pipeline stage.

Each executor wraps the corresponding app's functionality and returns
structured DTOs. Executors are idempotent and don't call downstream stages.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
    StageContext,
)

logger = logging.getLogger(__name__)


class BaseExecutor(ABC):
    """Base class for stage executors."""

    @abstractmethod
    def execute(self, ctx: StageContext) -> Any:
        """Execute the stage and return a result DTO."""
        raise NotImplementedError


class IngestExecutor(BaseExecutor):
    """
    Stage 1: Ingest executor.

    Wraps apps.alerts to parse/validate inbound payloads,
    normalize into incident triggers, and create/update records.
    """

    def execute(self, ctx: StageContext) -> IngestResult:
        """Execute alert ingestion."""
        start_time = time.perf_counter()
        result = IngestResult()

        try:
            from apps.alerts.models import Alert
            from apps.alerts.services import AlertOrchestrator

            payload = ctx.payload
            driver = payload.get("driver")

            if not isinstance(payload.get("payload"), dict):
                result.errors.append("payload must be a JSON object")
                return result

            orchestrator = AlertOrchestrator()
            proc_result = orchestrator.process_webhook(
                payload.get("payload", {}),
                driver=driver,
            )

            # Populate result
            result.alerts_created = proc_result.alerts_created
            result.alerts_updated = proc_result.alerts_updated
            result.alerts_resolved = proc_result.alerts_resolved
            result.incidents_created = proc_result.incidents_created
            result.incidents_updated = proc_result.incidents_updated
            result.errors = list(proc_result.errors)
            result.source = ctx.source

            # Find incident ID from latest alert
            latest_alert = Alert.objects.order_by("-received_at").select_related("incident").first()
            if latest_alert and latest_alert.incident_id:
                result.incident_id = latest_alert.incident_id
                result.alert_fingerprint = latest_alert.fingerprint
                result.severity = latest_alert.severity

            # Generate payload reference (hash-based, no secrets)
            result.normalized_payload_ref = f"payload:{ctx.trace_id}:{ctx.run_id}:ingest"

        except Exception as e:
            logger.exception("Error in IngestExecutor")
            result.errors.append(f"Ingest error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result


class CheckExecutor(BaseExecutor):
    """
    Stage 2: Check executor.

    Wraps apps.checkers to run diagnostics associated with the incident.
    """

    def execute(self, ctx: StageContext) -> CheckResult:
        """Execute checker diagnostics."""
        start_time = time.perf_counter()
        result = CheckResult()

        try:
            from apps.alerts.check_integration import CheckAlertBridge

            bridge = CheckAlertBridge()
            payload = ctx.payload

            check_names = payload.get("checker_names")
            checker_configs = payload.get("checker_configs")
            labels = payload.get("labels")

            bridge_result = bridge.run_checks_and_alert(
                checker_names=check_names,
                checker_configs=checker_configs,
                labels=labels,
            )

            result.checks_run = bridge_result.checks_run
            result.checks_passed = bridge_result.checks_run - len(bridge_result.errors)
            result.checks_failed = len(bridge_result.errors)
            result.errors = list(bridge_result.errors)

            # Store checks in structured format
            result.checks = []
            if hasattr(bridge_result, "check_results"):
                for check in bridge_result.check_results:
                    result.checks.append(
                        {
                            "name": getattr(check, "name", "unknown"),
                            "status": getattr(check, "status", "unknown"),
                            "value": getattr(check, "value", None),
                        }
                    )

            # Generate output reference
            result.checker_output_ref = f"checker:{ctx.trace_id}:{ctx.run_id}:check"

        except Exception as e:
            logger.exception("Error in CheckExecutor")
            result.errors.append(f"Check error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result


class AnalyzeExecutor(BaseExecutor):
    """
    Stage 3: Analyze executor.

    Wraps apps.intelligence to produce AI-powered analysis and recommendations.
    Supports fallback when AI is unavailable.
    """

    def __init__(self, fallback_enabled: bool = True):
        self.fallback_enabled = fallback_enabled

    def execute(self, ctx: StageContext) -> AnalyzeResult:
        """Execute intelligence analysis."""
        start_time = time.perf_counter()
        result = AnalyzeResult()

        try:
            from apps.intelligence.providers import get_provider

            payload = ctx.payload
            provider_name = payload.get("provider", "local")
            provider_config = payload.get("provider_config", {})

            provider = get_provider(provider_name, **(provider_config or {}))
            incident_id = ctx.incident_id

            recommendations = []
            if incident_id:
                from apps.alerts.models import Incident

                incident = Incident.objects.filter(id=incident_id).first()
                if incident:
                    recommendations = provider.analyze(incident)
            else:
                recommendations = provider.get_recommendations()

            # Populate result
            recs_list: list[dict[str, Any]] = []
            for r in recommendations:
                if hasattr(r, "to_dict"):
                    recs_list.append(r.to_dict())
                elif isinstance(r, dict):
                    recs_list.append(r)
                else:
                    # Convert object attributes to dict
                    recs_list.append(vars(r) if hasattr(r, "__dict__") else {"value": str(r)})
            result.recommendations = recs_list
            result.model_info = {"provider": provider_name}

            if recommendations:
                first_rec = recommendations[0]
                result.summary = getattr(first_rec, "title", "") or getattr(
                    first_rec, "summary", ""
                )
                result.probable_cause = getattr(first_rec, "description", "")
                result.actions = [
                    getattr(r, "action", "") for r in recommendations if hasattr(r, "action")
                ]
                result.confidence = 0.8  # Default confidence

            # Generate output reference
            result.ai_output_ref = f"intelligence:{ctx.trace_id}:{ctx.run_id}:analyze"

        except Exception as e:
            logger.exception("Error in AnalyzeExecutor")
            error_msg = f"Analyze error: {str(e)}"
            result.errors.append(error_msg)

            # Apply fallback if enabled
            if self.fallback_enabled:
                result.fallback_used = True
                result.summary = "AI analysis unavailable"
                result.probable_cause = "Unable to determine - AI provider error"
                result.actions = ["Manual investigation required"]
                result.confidence = 0.0
                result.errors = []  # Clear errors since we're using fallback
                logger.info(
                    f"Using fallback analysis due to error: {error_msg}",
                    extra={"trace_id": ctx.trace_id},
                )

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result


class NotifyExecutor(BaseExecutor):
    """
    Stage 4: Notify executor.

    Wraps apps.notify to dispatch notifications via configured channels.
    Uses idempotency keys to prevent duplicate messages.
    """

    def execute(self, ctx: StageContext) -> NotifyResult:
        """Execute notification dispatch."""
        start_time = time.perf_counter()
        result = NotifyResult()

        try:
            from apps.notify.drivers.base import NotificationMessage
            from apps.notify.views import DRIVER_REGISTRY

            payload = ctx.payload
            previous = ctx.previous_results

            driver_name = payload.get("notify_driver", "generic")
            config = payload.get("notify_config", {})
            channel = payload.get("notify_channel", "default")

            # Build notification message from intelligence results
            intelligence = previous.get("analyze", {})
            title = "Incident Analysis"
            message_body = "No recommendations available."
            severity = "info"

            recs = intelligence.get("recommendations", [])
            if recs:
                title = recs[0].get("title", title)
                lines = []
                for r in recs[:10]:
                    prio = r.get("priority", "")
                    r_title = r.get("title", "")
                    desc = r.get("description", "")
                    lines.append(f"- [{prio}] {r_title}: {desc}")
                message_body = "\n".join(lines)

                # Map priority to severity
                max_prio = {"critical": 3, "high": 2, "medium": 1, "low": 0}
                weight = -1
                for r in recs:
                    weight = max(weight, max_prio.get((r.get("priority") or "").lower(), 0))
                severity = "critical" if weight >= 3 else "warning" if weight >= 2 else "info"

            # Handle fallback case
            if intelligence.get("fallback_used"):
                title = "Incident Alert (AI Unavailable)"
                message_body = (
                    f"AI analysis was unavailable.\n\n"
                    f"Summary: {intelligence.get('summary', 'N/A')}\n"
                    f"Probable cause: {intelligence.get('probable_cause', 'N/A')}\n"
                    f"Actions: {', '.join(intelligence.get('actions', ['Manual investigation required']))}"
                )

            driver_cls = DRIVER_REGISTRY.get(driver_name)
            if driver_cls is None:
                result.errors.append(
                    f"Unknown notify driver: {driver_name}. Available: {list(DRIVER_REGISTRY.keys())}"
                )
                return result

            # driver_cls is a concrete class from the registry, not the abstract base
            driver_instance = driver_cls()  # type: ignore[abstract]
            if not driver_instance.validate_config(config):
                result.errors.append(f"Invalid configuration for notify driver: {driver_name}")
                return result

            # Create idempotency key
            idempotency_key = f"{ctx.trace_id}:{ctx.run_id}:notify:{channel}"

            msg = NotificationMessage(
                title=title,
                message=message_body,
                severity=severity,
                channel=channel,
                tags={
                    "trace_id": ctx.trace_id,
                    "run_id": ctx.run_id,
                    "incident_id": str(ctx.incident_id) if ctx.incident_id else "",
                    "idempotency_key": idempotency_key,
                },
                context={
                    "incident_id": ctx.incident_id,
                    "source": ctx.source,
                    "environment": ctx.environment,
                    "ingest": previous.get("ingest"),
                    "check": previous.get("check"),
                    "intelligence": intelligence,
                },
            )

            result.channels_attempted = 1
            send_result = driver_instance.send(msg, config)

            # Record delivery
            delivery = {
                "driver": driver_name,
                "channel": channel,
                "status": "success" if send_result.get("status") == "success" else "failed",
                "provider_id": send_result.get("message_id", ""),
                "response": send_result,
            }
            result.deliveries.append(delivery)

            if delivery["status"] == "success":
                result.channels_succeeded = 1
                if delivery["provider_id"]:
                    result.provider_ids.append(delivery["provider_id"])
            else:
                result.channels_failed = 1
                result.errors.append(
                    f"Delivery failed: {send_result.get('message', 'Unknown error')}"
                )

            # Generate output reference
            result.notify_output_ref = f"notify:{ctx.trace_id}:{ctx.run_id}:notify"

        except Exception as e:
            logger.exception("Error in NotifyExecutor")
            result.errors.append(f"Notify error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result
