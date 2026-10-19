"""Generic/custom notification driver."""

import json
import logging
import urllib.error
from types import MappingProxyType
from typing import Any
from urllib.request import Request  # noqa: TID251 — Request is a data object, not urlopen

from django.conf import settings

from apps.notify.config_fields import ConfigField, timeout_field
from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage
from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError

logger = logging.getLogger(__name__)


class GenericNotifyDriver(BaseNotifyDriver):
    """Generic driver for custom notification integrations."""

    name = "generic"
    label = "Generic webhook"
    credential_aliases = MappingProxyType({"webhook_url": "endpoint"})
    config_fields = (
        ConfigField("endpoint", "Endpoint URL", "url", secret=True),
        ConfigField(
            "method",
            "HTTP method",
            "choice",
            default="POST",
            choices=(("POST", "POST"), ("PUT", "PUT"), ("PATCH", "PATCH")),
        ),
        ConfigField(
            "headers",
            "Headers",
            "headers",
            secret=True,
            help="Sent with every request, added to the content type and user agent.",
        ),
        timeout_field(),
        ConfigField(
            "disabled",
            "Disabled",
            "bool",
            default=False,
            help="Accept every message and send nothing.",
        ),
    )

    def validate_config(self, config: dict[str, Any]) -> bool:
        # Allow empty/default config to mean "notifications disabled" (no-op)
        if not config or config.get("disabled"):
            return True

        # Require at least an endpoint URL
        if "endpoint" not in config and "webhook_url" not in config:
            return False

        # Validate URL format
        url = config.get("endpoint") or config.get("webhook_url", "")
        return url.startswith("http://") or url.startswith("https://")

    def _build_payload(
        self, message: NotificationMessage, config: dict[str, Any]
    ) -> dict[str, Any]:
        # Use centralized preparation which renders templates and composes incident
        prepared = self._prepare_notification(message, config)

        # Template must provide payload structure (as JSON)
        if prepared.get("payload_obj"):
            return prepared["payload_obj"]

        # If payload_raw (string) exists but not JSON, wrap it
        if prepared.get("payload_raw"):
            return {
                "title": message.title,
                "message": prepared.get("payload_raw"),
            }

        raise ValueError("Generic driver payload template required but not rendered")

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        if not self.validate_config(config):
            return {
                "success": False,
                "error": "Invalid configuration (endpoint or webhook_url required)",
            }

        # No-op: notifications are disabled or config is empty
        if not config or config.get("disabled"):
            return {"success": True, "message_id": None, "metadata": {"disabled": True}}

        endpoint = config.get("endpoint") or config.get("webhook_url")
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        timeout = config.get("timeout", 30)

        try:
            payload = self._build_payload(message, config)
            payload_json = json.dumps(payload).encode("utf-8")

            request_headers = {
                "Content-Type": "application/json",
                "User-Agent": "ServerMonitoring/1.0",
            }
            request_headers.update(headers)

            request = Request(
                str(endpoint),
                data=payload_json if method in ("POST", "PUT", "PATCH") else None,
                headers=request_headers,
                method=method,
            )

            with safe_urlopen(
                request, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                status_code = response.getcode()

                try:
                    response_data = json.loads(response_body)
                except json.JSONDecodeError:
                    response_data = {"raw": response_body}

                logger.info(f"Generic notification sent to {endpoint}: {status_code}")

                return {
                    "success": True,
                    "message_id": f"generic_{hash(str(endpoint) + message.title) & 0x7FFFFFFF:08x}",
                    "metadata": {
                        "endpoint": endpoint,
                        "method": method,
                        "status_code": status_code,
                        "response": response_data,
                    },
                }

        except URLNotAllowedError:
            return {"success": False, "error": "Endpoint URL not allowed by security policy"}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            logger.error(f"Generic HTTP error {e.code}: {error_body}")
            return {
                "success": False,
                "error": f"HTTP error ({e.code}): {error_body}",
                "status_code": e.code,
            }
        except urllib.error.URLError as e:
            return self._handle_url_error(e, "Generic")
        except Exception as e:
            return self._handle_exception(e, "Generic", "send notification")
