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

            incident = None
            if incident_id:
                from apps.alerts.models import Incident

                incident = Incident.objects.filter(id=incident_id).first()

            recommendations = provider.run(
                incident=incident,
                trace_id=ctx.trace_id,
                pipeline_run_id=ctx.run_id,
                provider_config=provider_config,
            )

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

            # Read requested notify_channel from payload (None if not provided).
            # We don't finalize the channel until we've resolved provider config
            # because a DB-stored channel config may provide a preferred channel
            # (for example, Slack channel name).
            requested_channel = payload.get("notify_channel")

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

            # Centralize provider/channel selection via NotifySelector
            from apps.notify.services import NotifySelector

            requested = payload.get("notify_driver")
            payload_config = payload.get("notify_config", {}) or {}

            (
                provider_name,
                config,
                selected_label,
                driver_cls,
                channel_obj,
                final_channel,
            ) = NotifySelector.resolve(requested, payload_config, requested_channel)

            # Note: do NOT treat payload.notify_channel as a channel-name selector
            # for choosing a NotificationChannel record. The selection priority is:
            # 1) If payload.notify_driver matches a NotificationChannel.name -> use DB channel
            # 2) If payload.notify_driver omitted -> pick first active NotificationChannel
            # 3) Otherwise treat payload.notify_driver as a provider key and use payload.notify_config
            # The payload.notify_channel is only a hint for the message destination (e.g. Slack channel)
            # and should not be used to select the provider. This avoids overlapping semantics.

            # Use final_channel computed by selector
            channel = final_channel

            # Create idempotency key
            idempotency_key = f"{ctx.trace_id}:{ctx.run_id}:notify:{channel}"

            # Ensure we have a concrete driver class (selector returns None when unknown)
            if driver_cls is None:
                result.errors.append(
                    f"Unknown notify driver/provider: {provider_name}. Available: {list(DRIVER_REGISTRY.keys())}"
                )
                return result

            # Build NotificationMessage using the base dataclass fields. Put
            # idempotency info into tags and the intelligence/check output into context.
            # Build human-readable summaries for previous stages to include in
            # the notification context (keeps messages concise and useful).
            ingest_prev = previous.get("ingest") or {}
            if isinstance(ingest_prev, dict):
                ingest_lines = ["**Ingest Summary**"]
                ingest_lines.append(f"- incident_id: `{ingest_prev.get('incident_id')}`")
                ingest_lines.append(f"- severity: {ingest_prev.get('severity')}")
                ingest_lines.append(f"- source: {ingest_prev.get('source')}")
                ingest_lines.append(f"- alerts_created: {ingest_prev.get('alerts_created', 0)}")
                ingest_lines.append(f"- alerts_updated: {ingest_prev.get('alerts_updated', 0)}")
                ingest_lines.append(f"- alerts_resolved: {ingest_prev.get('alerts_resolved', 0)}")
                ingest_lines.append(
                    f"- incidents_created: {ingest_prev.get('incidents_created', 0)}"
                )
                ingest_lines.append(
                    f"- incidents_updated: {ingest_prev.get('incidents_updated', 0)}"
                )
                ingest_lines.append(
                    f"- duration_ms: `{round(float(ingest_prev.get('duration_ms', 0.0)), 2)}`"
                )
                ingest_md = "\n".join(ingest_lines)
            else:
                ingest_md = "**Ingest Summary**\n```\n" + str(ingest_prev) + "\n```"

            check_prev = previous.get("check") or {}
            if isinstance(check_prev, dict):
                check_lines = ["**Check Summary**"]
                check_lines.append(f"- checks_run: {check_prev.get('checks_run', 0)}")
                check_lines.append(f"- passed: {check_prev.get('checks_passed', 0)}")
                check_lines.append(f"- failed: {check_prev.get('checks_failed', 0)}")
                cof = check_prev.get("checker_output_ref")
                if cof:
                    check_lines.append(f"- checker_output_ref: `{cof}`")
                check_lines.append(
                    f"- duration_ms: `{round(float(check_prev.get('duration_ms', 0.0)), 2)}`"
                )
                check_md = "\n".join(check_lines)
            else:
                check_md = "**Check Summary**\n```\n" + str(check_prev) + "\n```"

            # Intelligence -> markdown: summary, probable cause, recommendations count and top processes
            intelligence_prev = intelligence or {}
            if isinstance(intelligence_prev, dict):
                intel_lines = ["**Intelligence Summary**"]
                if intelligence_prev.get("summary"):
                    intel_lines.append(f"- summary: {intelligence_prev.get('summary')}")
                if intelligence_prev.get("probable_cause"):
                    intel_lines.append(
                        f"- probable_cause: {intelligence_prev.get('probable_cause')}"
                    )
                recs = intelligence_prev.get("recommendations") or []
                intel_lines.append(f"- recommendations: {len(recs)}")

                # top processes (if present in first recommendation details)
                top_proc_lines = []
                if recs and isinstance(recs, list) and isinstance(recs[0], dict):
                    details = recs[0].get("details", {}) or {}
                    top = details.get("top_processes") or []
                    if isinstance(top, list) and top:
                        top_proc_lines.append("- top_processes:")
                        for p in top[:5]:
                            pid = p.get("pid")
                            name = p.get("name") or (p.get("cmdline") or "")
                            cpu = p.get("cpu_percent")
                            try:
                                cpu_s = f"{float(cpu):.1f}%" if cpu is not None else ""
                            except Exception:
                                cpu_s = str(cpu)
                            top_proc_lines.append(f"  - `{pid}` {name} — {cpu_s}")
                intel_md = "\n".join(intel_lines + top_proc_lines)
            else:
                intel_md = "**Intelligence Summary**\n```\n" + str(intelligence_prev) + "\n```"

            # Build a single Markdown body that contains the message and
            # the human-readable summaries. This ensures all drivers receive
            # the same readable content and channels that support Markdown
            # (like Slack) will render it nicely.
            # Attempt to render a configured notification template. Template
            # can come from channel config (selected channel_obj) or payload
            # payload.notify_config.template. If present, render it with a
            # rich context; otherwise fall back to building md_body as before.
            from apps.notify.templating import render_template

            template_spec = None
            # Priority: DB channel config -> payload.notify_config
            if channel_obj and (channel_obj.config or {}).get("template"):
                template_spec = (channel_obj.config or {}).get("template")
                logger.debug("NotifyExecutor: using channel_obj.config.template=%r", template_spec)
            elif payload_config.get("template"):
                template_spec = payload_config.get("template")
                logger.debug("NotifyExecutor: using payload_config.template=%r", template_spec)

            # Build the rendering context
            render_ctx = {
                "title": title,
                "severity": severity,
                "ingest": ingest_prev,
                "check": check_prev,
                "intelligence": intelligence_prev,
                "recommendations": (
                    intelligence_prev.get("recommendations")
                    if isinstance(intelligence_prev, dict)
                    else []
                ),
                "incident_id": ctx.incident_id,
                "source": ctx.source,
                "environment": ctx.environment,
            }

            if template_spec:
                try:
                    md_body = render_template(template_spec, render_ctx) or ""
                    logger.debug("NotifyExecutor: rendered template_spec len=%d", len(md_body))
                except Exception:
                    logger.exception("Error rendering notification template")
                    # fallback to default composition
                    md_parts = []
                    if message_body:
                        md_parts.append(message_body)
                    if ingest_md:
                        md_parts.append(ingest_md)
                    if check_md:
                        md_parts.append(check_md)
                    if intel_md:
                        md_parts.append(intel_md)

                    md_body = "\n\n---\n\n".join(md_parts).strip()
            else:
                md_parts = []
                if message_body:
                    md_parts.append(message_body)
                if ingest_md:
                    md_parts.append(ingest_md)
                if check_md:
                    md_parts.append(check_md)
                if intel_md:
                    md_parts.append(intel_md)

                md_body = "\n\n---\n\n".join(md_parts).strip()

            message = NotificationMessage(
                title=title,
                message=md_body,
                severity=severity,
                channel=channel,
                tags={
                    "trace_id": ctx.trace_id,
                    "run_id": ctx.run_id,
                    "idempotency_key": idempotency_key,
                },
                context={
                    "incident_id": ctx.incident_id,
                    "source": ctx.source,
                    "environment": ctx.environment,
                    "ingest": ingest_md,
                    "check": check_md,
                    "intelligence": intel_md,
                },
            )

            # Store prepared message payload as a plain dict to avoid coupling DTOs
            # to driver-specific classes.
            from dataclasses import asdict

            result.messages = [asdict(message)]

            # Instantiate driver and send the notification
            driver_instance = driver_cls()
            # Validate config before sending
            if not driver_instance.validate_config(config):
                result.errors.append(
                    f"Invalid configuration for notify provider: {provider_name} (selected: {selected_label})"
                )
                return result

            result.channels_attempted = 1
            try:
                send_result = driver_instance.send(message, config)
            except Exception as e:
                logger.exception("Error sending notification via driver")
                result.channels_failed = 1
                result.errors.append(f"Send error: {str(e)}")
                # Record a failed delivery with exception message
                delivery = {
                    "driver": selected_label or provider_name,
                    "provider": provider_name,
                    "channel": channel,
                    "status": "failed",
                    "provider_id": "",
                    "response": {"error": str(e)},
                }
                result.deliveries.append(delivery)
            else:
                # Normalize success flag
                success = bool(
                    send_result.get("success") or (send_result.get("status") == "success")
                )
                delivery = {
                    "driver": selected_label or provider_name,
                    "provider": provider_name,
                    "channel": channel,
                    "status": "success" if success else "failed",
                    "provider_id": send_result.get("message_id", "")
                    or send_result.get("provider_id", ""),
                    "response": send_result,
                }
                result.deliveries.append(delivery)
                if success:
                    result.channels_succeeded = 1
                    pid = delivery.get("provider_id")
                    if pid:
                        # Normalize provider id(s) into strings before appending.
                        # Support single string, iterable of strings, or other types.
                        if isinstance(pid, str):
                            result.provider_ids.append(pid)
                        elif isinstance(pid, (list, tuple, set)):
                            for item in pid:
                                result.provider_ids.append(str(item))
                        else:
                            # Fallback: coerce to string
                            result.provider_ids.append(str(pid))
                else:
                    result.channels_failed = 1
                    result.errors.append(
                        f"Delivery failed: {send_result.get('error') or send_result.get('message', '')}"
                    )

            # Generate output reference
            result.notify_output_ref = f"notify:{ctx.trace_id}:{ctx.run_id}:notify"

            # Log the notification dispatch
            logger.info(
                "Dispatched notification",
                extra={
                    "trace_id": ctx.trace_id,
                    "run_id": ctx.run_id,
                    "provider": provider_name,
                    "channel": channel,
                    "title": title,
                    "severity": severity,
                    "idempotency_key": idempotency_key,
                },
            )

        except Exception as e:
            logger.exception("Error in NotifyExecutor")
            result.errors.append(f"Notify error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result
