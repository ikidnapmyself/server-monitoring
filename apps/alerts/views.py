"""
Webhook views for receiving alerts from external sources.
"""

import json
import logging
from typing import Any

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


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

            # Resolve the driver (from the URL or by sniffing the payload).
            from apps.alerts.drivers import detect_driver, get_driver

            resolved_driver = None
            if driver:
                try:
                    resolved_driver = get_driver(driver)
                except ValueError:
                    return JsonResponse(
                        {"status": "error", "message": "Unknown driver"},
                        status=400,
                    )
            else:
                resolved_driver = detect_driver(payload)

            # Interim (Slice A) rule: a driver whose payload already carries its own
            # diagnostics (e.g. cluster) tells the pipeline to skip the local CHECK
            # stage for this run. This is a driver property, not a source-string branch
            # in the orchestrator. Authentication is handled uniformly by the API-key
            # middleware; there is no per-driver signature check.
            if resolved_driver and getattr(resolved_driver, "skip_checkers", False):
                payload["skip_checkers"] = True

            # Durable ingest: record the run and return immediately. A drain
            # (manage.py process_inbox) processes it later. No inline pipeline,
            # no broker — a flood grows a bounded PENDING queue, not the heap.
            from apps.orchestration.orchestrator import PipelineOrchestrator

            run = PipelineOrchestrator().start_pipeline(payload=payload, source=driver or "unknown")
            logger.info(
                "Webhook recorded run %s (source=%s) for draining",
                run.run_id,
                driver or "unknown",
            )
            return JsonResponse(
                {"status": "accepted", "run_id": run.run_id},
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
