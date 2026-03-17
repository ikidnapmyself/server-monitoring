"""
Webhook views for receiving alerts from external sources.
"""

import json
import logging
import os
from typing import Any, cast

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.alerts.services import AlertOrchestrator

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

            # Verify webhook signature if configured
            from apps.alerts.drivers import detect_driver, get_driver

            sig_driver = None
            if driver:
                try:
                    sig_driver = get_driver(driver)
                except ValueError:
                    return JsonResponse(
                        {"status": "error", "message": "Unknown driver"},
                        status=400,
                    )
            else:
                sig_driver = detect_driver(payload)

            if sig_driver and sig_driver.signature_header:
                import os as _os

                secret_env = f"WEBHOOK_SECRET_{sig_driver.name.upper()}"
                secret = _os.environ.get(secret_env)
                if secret:
                    header_key = f"HTTP_{sig_driver.signature_header.upper().replace('-', '_')}"
                    sig_value = request.META.get(header_key)
                    if not sig_value or not sig_driver.verify_signature(
                        request.body, sig_value, secret
                    ):
                        return JsonResponse(
                            {"status": "error", "message": "Invalid webhook signature"},
                            status=403,
                        )

            # If Celery is enabled, enqueue the orchestration chain and return quickly.
            # (In tests/dev you can set CELERY_TASK_ALWAYS_EAGER=1 to run inline.)
            try:
                from django.conf import settings

                celery_eager = bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
            except Exception:
                celery_eager = False

            if os.environ.get("ENABLE_CELERY_ORCHESTRATION", "1") == "1" and not celery_eager:
                try:
                    from apps.orchestration.tasks import run_pipeline_task

                    async_res = run_pipeline_task.delay(
                        payload=payload,
                        source=driver or "unknown",
                    )
                    return JsonResponse(
                        {
                            "status": "queued",
                            "pipeline_id": async_res.id,
                        },
                        status=202,
                    )
                except Exception as enqueue_err:
                    # If Celery isn't reachable (broker/result backend down), don't 500 the webhook.
                    # Fall back to synchronous processing.
                    logger.warning(
                        "Celery orchestration enqueue failed; falling back to sync processing: %s",
                        enqueue_err,
                    )

            # Fallback: synchronous processing (previous behavior)
            orchestrator = AlertOrchestrator()
            result = orchestrator.process_webhook(payload, driver=driver)

            # Build response
            response_data: dict[str, Any] = {
                "status": "success" if not result.has_errors else "partial",
                "alerts_created": result.alerts_created,
                "alerts_updated": result.alerts_updated,
                "alerts_resolved": result.alerts_resolved,
                "incidents_created": result.incidents_created,
                "incidents_updated": result.incidents_updated,
            }

            if result.has_errors:
                response_data["errors"] = cast(Any, result.errors)
                logger.warning(f"Webhook processing errors: {result.errors}")

            logger.info(
                f"Webhook processed: {result.total_processed} alerts "
                f"({result.alerts_created} new, {result.alerts_resolved} resolved)"
            )

            return JsonResponse(response_data)

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
