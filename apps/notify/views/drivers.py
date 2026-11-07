"""Notification driver listing endpoint."""

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt


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
