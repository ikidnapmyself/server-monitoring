"""
Views for the notify app.

Provides API endpoints for sending notifications via various drivers.
"""

import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver
from apps.notify.drivers.generic import GenericNotifyDriver
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver
from apps.notify.drivers.slack import SlackNotifyDriver

logger = logging.getLogger(__name__)


# Registry of available drivers
DRIVER_REGISTRY = {
    "email": EmailNotifyDriver,
    "slack": SlackNotifyDriver,
    "pagerduty": PagerDutyNotifyDriver,
    "generic": GenericNotifyDriver,
}


@method_decorator(csrf_exempt, name="dispatch")
class NotifyView(View):
    """
    API endpoint for sending notifications.

    POST /notify/send/
    POST /notify/send/<driver>/

    Accepts JSON payload:
    {
        "title": "Alert Title",
        "message": "Alert message body",
        "severity": "critical",  // critical, warning, info, success
        "channel": "ops-team",
        "tags": {"env": "production"},
        "context": {"cpu": 95.2},
        "config": {
            // Driver-specific configuration
        }
    }
    """

    def post(self, request, driver=None):
        """Handle notification send request."""
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

            # Validate required fields
            if "title" not in payload or "message" not in payload:
                return JsonResponse(
                    {"status": "error", "message": "Missing required fields: title, message"},
                    status=400,
                )

            # Determine provider/channel via centralized selector
            from apps.notify.services import NotifySelector

            requested = driver or payload.get("driver")
            payload_config = payload.get("config", {})
            requested_channel = payload.get("channel")

            (
                provider_name,
                config,
                selected_label,
                driver_class,
                channel_obj,
                final_channel,
            ) = NotifySelector.resolve(requested, payload_config, requested_channel)

            if driver_class is None:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": f"Unknown driver/provider: {provider_name}",
                        "available_drivers": list(DRIVER_REGISTRY.keys()),
                    },
                    status=400,
                )

            # Build notification message. Use final_channel from selector so that
            # DB-configured channel destinations take precedence over the payload hint.
            message = NotificationMessage(
                title=payload["title"],
                message=payload["message"],
                severity=payload.get("severity", "info"),
                channel=final_channel,
                tags=payload.get("tags", {}),
                context=payload.get("context", {}),
            )

            # Instantiate and validate driver
            driver_instance = driver_class()

            if not driver_instance.validate_config(config):
                return JsonResponse(
                    {
                        "status": "error",
                        "message": f"Invalid configuration for {provider_name} driver",
                    },
                    status=400,
                )

            # Send notification
            result = driver_instance.send(message, config)

            if result.get("success"):
                logger.info(
                    f"Notification sent via {provider_name} ({selected_label}): {message.title}"
                )
                return JsonResponse(
                    {
                        "status": "success",
                        "driver": provider_name,
                        "channel": selected_label,
                        "message_id": result.get("message_id"),
                        "metadata": result.get("metadata", {}),
                    }
                )
            else:
                logger.warning(
                    f"Notification failed via {provider_name} ({selected_label}): {result.get('error')}"
                )
                return JsonResponse(
                    {
                        "status": "error",
                        "driver": provider_name,
                        "channel": selected_label,
                        "message": result.get("error", "Unknown error"),
                    },
                    status=500,
                )

        except Exception as e:
            logger.exception("Unexpected error sending notification")
            return JsonResponse(
                {"status": "error", "message": str(e)},
                status=500,
            )

    def get(self, request, driver=None):
        """Health check and driver info endpoint."""
        if driver:
            if driver not in DRIVER_REGISTRY:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": f"Unknown driver: {driver}",
                        "available_drivers": list(DRIVER_REGISTRY.keys()),
                    },
                    status=404,
                )
            return JsonResponse(
                {
                    "status": "ok",
                    "driver": driver,
                    "message": f"Notify endpoint ready for {driver} driver",
                }
            )

        return JsonResponse(
            {
                "status": "ok",
                "message": "Notify endpoint is ready",
                "available_drivers": list(DRIVER_REGISTRY.keys()),
            }
        )


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


@method_decorator(csrf_exempt, name="dispatch")
class DriversView(View):
    """
    API endpoint for listing available notification drivers.

    GET /notify/drivers/
    GET /notify/drivers/<driver>/
    """

    DRIVER_INFO = {
        "email": {
            "name": "email",
            "description": "Send notifications via SMTP email",
            "required_config": ["smtp_host", "from_address"],
            "optional_config": [
                "smtp_port",
                "use_tls",
                "use_ssl",
                "username",
                "password",
                "to_addresses",
                "timeout",
            ],
        },
        "slack": {
            "name": "slack",
            "description": "Send notifications to Slack via webhooks",
            "required_config": ["webhook_url"],
            "optional_config": ["channel", "username", "icon_emoji", "timeout"],
        },
        "pagerduty": {
            "name": "pagerduty",
            "description": "Create incidents in PagerDuty via Events API v2",
            "required_config": ["integration_key"],
            "optional_config": ["dedup_key", "event_action", "client", "client_url", "timeout"],
        },
        "generic": {
            "name": "generic",
            "description": "Send notifications to a custom HTTP endpoint",
            "required_config": ["endpoint"],
            "optional_config": ["method", "headers", "timeout", "payload_template"],
        },
    }

    def get(self, request, driver=None):
        """List drivers or get specific driver info."""
        if driver:
            if driver not in self.DRIVER_INFO:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": f"Unknown driver: {driver}",
                        "available_drivers": list(self.DRIVER_INFO.keys()),
                    },
                    status=404,
                )
            return JsonResponse(
                {
                    "status": "ok",
                    "driver": self.DRIVER_INFO[driver],
                }
            )

        return JsonResponse(
            {
                "status": "ok",
                "drivers": list(self.DRIVER_INFO.values()),
            }
        )
