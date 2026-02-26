# apps/orchestration/nodes/ingest.py
"""Ingest node handler for alert ingestion."""

import logging
import time
from typing import Any, Dict

from django.utils import timezone

from apps.orchestration.nodes.base import (
    BaseNodeHandler,
    NodeContext,
    NodeResult,
    NodeType,
)

logger = logging.getLogger(__name__)


class IngestNodeHandler(BaseNodeHandler):
    """
    Node handler for alert ingestion.

    Wraps the existing AlertOrchestrator to parse incoming
    alert payloads and create/update Incidents and Alerts.

    This is the entry point for alert-triggered pipelines.
    """

    node_type = NodeType.INGEST
    name = "ingest"

    def execute(self, ctx: NodeContext, config: Dict[str, Any]) -> NodeResult:
        """Execute alert ingestion."""
        start_time = time.perf_counter()
        result = NodeResult(
            node_id=config.get("id", "ingest"),
            node_type="ingest",
        )

        try:
            from apps.alerts.services import AlertOrchestrator

            payload = ctx.payload
            driver = config.get("driver") or payload.get("driver")
            alert_payload = payload.get("payload", payload)

            # Validate payload
            if not isinstance(alert_payload, dict):
                result.errors.append("payload must be a JSON object")
                result.duration_ms = (time.perf_counter() - start_time) * 1000
                return result

            # Record time before processing so we can filter to alerts
            # created during this call and avoid a concurrency race.
            before_process = timezone.now()

            # Process the webhook
            orchestrator = AlertOrchestrator()
            proc_result = orchestrator.process_webhook(
                alert_payload,
                driver=driver,
            )

            # Populate result
            result.output = {
                "alerts_created": proc_result.alerts_created,
                "alerts_updated": proc_result.alerts_updated,
                "alerts_resolved": proc_result.alerts_resolved,
                "incidents_created": proc_result.incidents_created,
                "incidents_updated": proc_result.incidents_updated,
                "source": ctx.source,
            }

            # Copy errors from processing
            if proc_result.errors:
                result.errors.extend(proc_result.errors)

            # Find the incident ID from alerts created during this run.
            # Filtering by received_at >= before_process avoids picking up an
            # unrelated alert inserted concurrently by another process.
            from apps.alerts.models import Alert

            latest_alert = (
                Alert.objects.filter(received_at__gte=before_process)
                .order_by("-received_at")
                .select_related("incident")
                .first()
            )
            if latest_alert and latest_alert.incident_id:
                result.output["incident_id"] = latest_alert.incident_id
                result.output["alert_fingerprint"] = latest_alert.fingerprint
                result.output["severity"] = latest_alert.severity

                # IMPORTANT: Update the context so subsequent nodes have incident_id
                # This is done by the orchestrator reading from output

        except Exception as e:
            logger.exception("Error in IngestNodeHandler: %s", e)
            result.errors.append(f"Ingest error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def validate_config(self, config: Dict[str, Any]) -> list[str]:
        """Validate ingest node configuration."""
        # Ingest node has no required config - driver can come from payload
        return []
