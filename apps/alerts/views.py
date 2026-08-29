"""
Webhook views for receiving alerts from external sources.
"""

import json
import logging
import uuid
from typing import Any

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# Ingest now runs on the web worker rather than in the drain, so an unbounded
# request body is unbounded work on the request thread: parsing it, and then one
# alert write per element it contains. The cap bounds that before a byte of it is
# interpreted.
#
# 1 MiB is roughly a hundred times the largest thing a legitimate sender pushes: a
# cluster push carries one alert per checker (~14 today) at a few hundred bytes
# each, and a grouped Alertmanager notification is tens of KB. It also sits under
# Django's own DATA_UPLOAD_MAX_MEMORY_SIZE (2.5 MB default), so an oversized body
# gets this endpoint's honest 413 rather than a generic framework error.
MAX_PAYLOAD_BYTES = 1_048_576


@method_decorator(csrf_exempt, name="dispatch")
class AlertWebhookView(View):
    """
    Generic webhook endpoint for receiving alerts.

    POST /alerts/webhook/
    POST /alerts/webhook/<driver>/

    Accepts JSON payloads from various alert sources.
    The driver can be auto-detected or specified in the URL.
    """

    def post(self, request: Any, driver: str | None = None) -> JsonResponse:
        """Handle incoming alert webhook."""
        # Before anything else, including the JSON parse: see MAX_PAYLOAD_BYTES.
        # Deliberately outside the try below, so nothing is written and no
        # unexpected-error path can turn a refusal into a 500.
        if len(request.body) > MAX_PAYLOAD_BYTES:
            logger.warning(
                "Rejected oversized webhook body (%d bytes, cap %d, driver=%s)",
                len(request.body),
                MAX_PAYLOAD_BYTES,
                driver or "unknown",
            )
            return JsonResponse(
                {"status": "error", "message": "Payload too large"},
                status=413,
            )

        try:
            # Parse JSON payload
            try:
                payload = json.loads(request.body)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON payload: {e}")
                return JsonResponse(
                    {"status": "error", "message": "Invalid JSON payload"},
                    status=400,
                )

            # Reject an unknown driver name before doing any work. Authentication is
            # handled uniformly by the API-key middleware; there is no per-driver
            # signature check. A request with no driver in the URL is sniffed by
            # ``process_webhook`` below.
            from apps.alerts.drivers import DRIVER_REGISTRY

            if driver and driver not in DRIVER_REGISTRY:
                return JsonResponse(
                    {"status": "error", "message": "Unknown driver"},
                    status=400,
                )

            # Register/refresh the sending node first, and independently of the
            # ingest. A push proves the sender is alive whatever its alerts turn out
            # to be — undetectable, empty, or unchanged — so the node registry must
            # not depend on them.
            from apps.alerts.services import AlertOrchestrator, register_pushing_node

            register_pushing_node(payload, driver)

            # Producing an alert is not a pipeline stage. The webhook writes the
            # alerts itself, lets incidents form, and then enqueues one PENDING run
            # per incident that materially changed; ``manage.py process_inbox``
            # drains those.
            #
            # This is still "no inline pipeline": what moved onto the request thread
            # is the bounded alert write (capped above), while checkers, inference
            # and delivery — the slow, unbounded stages durable ingest was built to
            # keep off it — stay queued. Concurrency here is already capped by the
            # worker count, so a flood queues at the web tier and then grows a
            # bounded PENDING queue exactly as before.
            from apps.orchestration.intake import enqueue_for
            from apps.orchestration.models import PipelineOrigin

            trace_id = str(uuid.uuid4())
            source = driver or "unknown"
            proc_result = AlertOrchestrator(trace_id=trace_id).process_webhook(
                payload, driver=driver
            )

            # Nothing understood the payload: no driver claimed it, so nothing was
            # parsed and nothing was written. That used to fail invisibly in the
            # drain; the sender is the only one who can fix it, and a retry would
            # fail identically, so it is told to stop. Read off the flag the
            # orchestrator sets rather than matching its error text.
            if not proc_result.driver_resolved:
                logger.warning(
                    "No driver could handle the webhook payload (driver=%s, trace_id=%s): %s",
                    source,
                    trace_id,
                    "; ".join(proc_result.errors),
                )
                return JsonResponse(
                    {"status": "error", "message": "Could not detect a driver for the payload"},
                    status=400,
                )

            # A driver understood it and we still wrote nothing: the fault is ours,
            # not the sender's. 400 would tell it to stop retrying and the push would
            # be silently discarded, so this is the one ingest failure that must be a
            # 5xx — there is no partial write for a retry to duplicate.
            if proc_result.errors and not proc_result.alerts:
                logger.error(
                    "Webhook ingest wrote nothing (driver=%s, trace_id=%s): %s",
                    source,
                    trace_id,
                    "; ".join(proc_result.errors),
                )
                return JsonResponse(
                    {"status": "error", "message": "Failed to process payload"},
                    status=500,
                )

            # A payload that partially failed but wrote alerts is accepted: turning
            # it into a 5xx would have the sender retry and duplicate the work that
            # did land. The errors are logged, not swallowed.
            if proc_result.errors:
                logger.error(
                    "Webhook ingest reported errors (driver=%s, trace_id=%s): %s",
                    source,
                    trace_id,
                    "; ".join(proc_result.errors),
                )

            if not proc_result.alerts:
                # A misconfigured sender, not a failure: no rows, no run, no error.
                logger.warning(
                    "Webhook payload carried no alerts (driver=%s, trace_id=%s)",
                    source,
                    trace_id,
                )

            runs = enqueue_for(
                proc_result,
                trace_id=trace_id,
                origin=PipelineOrigin.INCOMING_WEBHOOK,
                source=source,
            )
            incident_ids = [run.incident_id for run in runs]
            logger.info(
                "Webhook ingested %d alert(s), queued %d incident run(s) (source=%s, trace_id=%s)",
                len(proc_result.alerts),
                len(runs),
                source,
                trace_id,
            )
            return JsonResponse(
                {"status": "accepted", "trace_id": trace_id, "incidents": incident_ids},
                status=202,
            )

        except Exception as e:
            logger.exception("Unexpected error processing webhook")
            return JsonResponse(
                {"status": "error", "message": str(e)},
                status=500,
            )

    def get(self, request: Any, driver: str | None = None) -> JsonResponse:
        """Health check endpoint."""
        return JsonResponse(
            {
                "status": "ok",
                "message": "Alert webhook endpoint is ready",
                "driver": driver or "auto-detect",
            }
        )
