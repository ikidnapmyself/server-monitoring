"""Batch notification send endpoint."""

import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.drivers.base import NotificationMessage

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class NotifyBatchView(View):
    """
    API endpoint for sending batch notifications.

    POST /notify/batch/

    Accepts JSON payload:
    {
        "notifications": [
            {
                "driver": "slack",
                "title": "Alert 1",
                "message": "Message 1",
                "config": {...}
            },
            {
                "driver": "email",
                "title": "Alert 2",
                "message": "Message 2",
                "config": {...}
            }
        ]
    }
    """

    def post(self, request):
        """Handle batch notification send request."""
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

            notifications = payload.get("notifications", [])
            if not notifications:
                return JsonResponse(
                    {"status": "error", "message": "No notifications provided"},
                    status=400,
                )

            results = []
            success_count = 0
            error_count = 0

            for idx, notif in enumerate(notifications):
                # Validate required fields
                if "title" not in notif or "message" not in notif:
                    results.append(
                        {
                            "index": idx,
                            "success": False,
                            "error": "Missing required fields: title, message",
                        }
                    )
                    error_count += 1
                    continue

                # Determine driver
                driver_name = notif.get("driver", "generic")
                if driver_name not in DRIVER_REGISTRY:
                    results.append(
                        {
                            "index": idx,
                            "success": False,
                            "error": f"Unknown driver: {driver_name}",
                        }
                    )
                    error_count += 1
                    continue

                # Build notification message
                message = NotificationMessage(
                    title=notif["title"],
                    message=notif["message"],
                    severity=notif.get("severity", "info"),
                    channel=notif.get("channel", "default"),
                    tags=notif.get("tags", {}),
                    context=notif.get("context", {}),
                )

                # Get driver configuration
                config = notif.get("config", {})

                # Instantiate driver and send
                driver_class = DRIVER_REGISTRY[driver_name]
                driver_instance = driver_class()

                if not driver_instance.validate_config(config):
                    results.append(
                        {
                            "index": idx,
                            "success": False,
                            "driver": driver_name,
                            "error": f"Invalid configuration for {driver_name} driver",
                        }
                    )
                    error_count += 1
                    continue

                result = driver_instance.send(message, config)

                if result.get("success"):
                    results.append(
                        {
                            "index": idx,
                            "success": True,
                            "driver": driver_name,
                            "message_id": result.get("message_id"),
                        }
                    )
                    success_count += 1
                else:
                    results.append(
                        {
                            "index": idx,
                            "success": False,
                            "driver": driver_name,
                            "error": result.get("error"),
                        }
                    )
                    error_count += 1

            # Determine overall status
            if error_count == 0:
                status = "success"
            elif success_count == 0:
                status = "error"
            else:
                status = "partial"

            logger.info(f"Batch notification: {success_count} succeeded, {error_count} failed")

            return JsonResponse(
                {
                    "status": status,
                    "total": len(notifications),
                    "success_count": success_count,
                    "error_count": error_count,
                    "results": results,
                }
            )

        except Exception as e:
            logger.exception("Unexpected error in batch notification")
            return JsonResponse(
                {"status": "error", "message": str(e)},
                status=500,
            )

    def get(self, request):
        """Health check endpoint."""
        return JsonResponse(
            {
                "status": "ok",
                "message": "Batch notify endpoint is ready",
                "available_drivers": list(DRIVER_REGISTRY.keys()),
            }
        )
