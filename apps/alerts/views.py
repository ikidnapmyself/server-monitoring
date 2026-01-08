"""
Webhook views for receiving alerts from external sources.
"""

import json
import logging

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

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

    def post(self, request, driver=None):
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

            # Process the payload
            orchestrator = AlertOrchestrator()
            result = orchestrator.process_webhook(payload, driver=driver)

            # Build response
            response_data = {
                "status": "success" if not result.has_errors else "partial",
                "alerts_created": result.alerts_created,
                "alerts_updated": result.alerts_updated,
                "alerts_resolved": result.alerts_resolved,
                "incidents_created": result.incidents_created,
                "incidents_updated": result.incidents_updated,
            }

            if result.has_errors:
                response_data["errors"] = result.errors
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

    def get(self, request, driver=None):
        """Health check endpoint."""
        return JsonResponse({
            "status": "ok",
            "message": "Alert webhook endpoint is ready",
            "driver": driver or "auto-detect",
        })

