"""Notify node handler — sends real notifications via configured channels."""

import logging
import time
from typing import Any, Dict

from apps.orchestration.nodes.base import BaseNodeHandler, NodeContext, NodeResult, NodeType

logger = logging.getLogger(__name__)


class NotifyNodeHandler(BaseNodeHandler):
    node_type = NodeType.NOTIFY
    name = "notify"

    def execute(self, ctx: NodeContext, config: Dict[str, Any]) -> NodeResult:
        from apps.notify.models import NotificationChannel
        from apps.notify.views import DRIVER_REGISTRY

        start_time = time.perf_counter()
        node_id = config.get("id", "notify")
        result = NodeResult(node_id=node_id, node_type="notify")

        # Accept both "drivers" (list) and "driver" (string) for backwards compat
        drivers = config.get("drivers") or []
        if not drivers:
            single = config.get("driver")
            if single:
                drivers = [single]

        if not drivers:
            result.errors.append("Missing 'drivers' or 'driver' in notify config")
            result.duration_ms = (time.perf_counter() - start_time) * 1000
            return result

        # Query active channels matching the configured driver types
        channels = list(NotificationChannel.objects.filter(driver__in=drivers, is_active=True))

        # Fallback: if no DB channels found, try NotifySelector for first active channel
        if not channels:
            from apps.notify.services import NotifySelector

            _, _, _, _, channel_obj, _ = NotifySelector.resolve(None)
            if channel_obj:
                channels = [channel_obj]
            else:
                result.errors.append(f"No active NotificationChannel found for drivers: {drivers}")
                result.duration_ms = (time.perf_counter() - start_time) * 1000
                return result

        # Build notification message from previous node outputs
        message = self._build_message(ctx, config)

        channels_attempted = 0
        channels_succeeded = 0
        channels_failed = 0
        deliveries = []

        for channel in channels:
            driver_cls = DRIVER_REGISTRY.get(channel.driver)
            if not driver_cls:
                logger.warning(
                    "Unknown driver type: %s for channel %s", channel.driver, channel.name
                )
                channels_attempted += 1
                channels_failed += 1
                deliveries.append(
                    {
                        "driver": channel.driver,
                        "channel": channel.name,
                        "status": "failed",
                        "error": f"Unknown driver: {channel.driver}",
                    }
                )
                continue

            driver_instance = driver_cls()  # type: ignore[abstract]
            channel_config = channel.config or {}

            if not driver_instance.validate_config(channel_config):
                channels_attempted += 1
                channels_failed += 1
                deliveries.append(
                    {
                        "driver": channel.driver,
                        "channel": channel.name,
                        "status": "failed",
                        "error": "Invalid driver configuration",
                    }
                )
                continue

            channels_attempted += 1
            try:
                send_result = driver_instance.send(message, channel_config)
                success = bool(send_result.get("success") or send_result.get("status") == "success")
                deliveries.append(
                    {
                        "driver": channel.driver,
                        "channel": channel.name,
                        "status": "success" if success else "failed",
                        "message_id": send_result.get("message_id", ""),
                        "error": send_result.get("error", "") if not success else "",
                    }
                )
                if success:
                    channels_succeeded += 1
                else:
                    channels_failed += 1
            except Exception as e:
                logger.exception("Error sending via %s channel %s", channel.driver, channel.name)
                channels_failed += 1
                deliveries.append(
                    {
                        "driver": channel.driver,
                        "channel": channel.name,
                        "status": "failed",
                        "error": str(e),
                    }
                )

        result.output = {
            "channels_attempted": channels_attempted,
            "channels_succeeded": channels_succeeded,
            "channels_failed": channels_failed,
            "deliveries": deliveries,
        }

        # Only add to errors if ALL channels failed
        if channels_attempted > 0 and channels_succeeded == 0:
            result.errors.append(f"All {channels_failed} notification channel(s) failed")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def _build_message(self, ctx: NodeContext, config: Dict[str, Any]):
        """Build a NotificationMessage from pipeline context."""
        from apps.notify.drivers.base import NotificationMessage

        title = "Pipeline Notification"
        severity = "info"
        body_parts = []

        # Gather info from previous node outputs
        for node_id, output in ctx.previous_outputs.items():
            if not isinstance(output, dict):
                continue

            # Context/checker results
            if "checks_run" in output:
                checks = output.get("results", {})
                failed_checks = [
                    f"  - {name}: {info.get('status', '?')} — {info.get('message', '')}"
                    for name, info in checks.items()
                    if info.get("status") in ("warning", "critical", "unknown")
                ]
                ok_checks = [name for name, info in checks.items() if info.get("status") == "ok"]

                # Derive severity from worst check result
                statuses = [info.get("status") for info in checks.values()]
                if "critical" in statuses:
                    severity = "critical"
                    title = "Health Check Alert — Critical"
                elif "warning" in statuses:
                    severity = "warning"
                    title = "Health Check Alert — Warning"
                else:
                    title = "Health Check Report"

                section = f"**Health Checks** ({output.get('checks_passed', 0)}/{output.get('checks_run', 0)} passed)"
                if failed_checks:
                    section += "\n" + "\n".join(failed_checks)
                if ok_checks:
                    section += f"\n  - OK: {', '.join(ok_checks)}"
                body_parts.append(section)

            # Intelligence results
            if "recommendations" in output or "summary" in output:
                section = "**Intelligence**"
                if output.get("summary"):
                    section += f"\n  Summary: {output['summary']}"
                if output.get("probable_cause"):
                    section += f"\n  Probable cause: {output['probable_cause']}"
                recs = output.get("recommendations", [])
                if recs:
                    section += f"\n  Recommendations: {len(recs)}"
                body_parts.append(section)

        body = "\n\n---\n\n".join(body_parts) if body_parts else "Pipeline completed."

        return NotificationMessage(
            title=title,
            message=body,
            severity=severity,
            tags={
                "trace_id": ctx.trace_id,
                "run_id": ctx.run_id,
            },
            context={
                "source": ctx.source,
                "environment": ctx.environment,
                "incident_id": ctx.incident_id,
            },
        )

    def validate_config(self, config: Dict[str, Any]) -> list[str]:
        errors = []
        drivers = config.get("drivers") or []
        driver = config.get("driver")
        if not drivers and not driver:
            errors.append("'drivers' (list) or 'driver' (string) is required for notify nodes")
        return errors
