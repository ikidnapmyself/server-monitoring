"""Generic/custom notification driver."""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

logger = logging.getLogger(__name__)


class GenericNotifyDriver(BaseNotifyDriver):
    """
    Generic driver for custom notification integrations.

    Configuration is flexible and depends on your custom backend:
    {
        "endpoint": "https://api.example.com/notify",
        "method": "POST",
        "headers": {
            "Authorization": "Bearer your-api-key",
            "X-Custom-Header": "value"
        },
        "timeout": 30,
        "payload_template": {
            "alert": "{title}",
            "body": "{message}",
            "level": "{severity}"
        }
    }
    """

    name = "generic"

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate generic driver configuration."""
        # Require at least an endpoint URL
        if "endpoint" not in config and "webhook_url" not in config:
            return False

        # Validate URL format
        url = config.get("endpoint") or config.get("webhook_url", "")
        return url.startswith("http://") or url.startswith("https://")

    def _build_payload(
        self, message: NotificationMessage, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Build the notification payload.

        If a payload_template is provided, use it for custom formatting.
        Otherwise, use a sensible default structure.
        """
        template = config.get("payload_template")

        if template:
            # Use custom template with substitutions
            return self._apply_template(template, message)

        # Default payload structure
        return {
            "title": message.title,
            "message": message.message,
            "severity": message.severity,
            "channel": message.channel,
            "tags": message.tags,
            "context": message.context,
        }

    def _apply_template(
        self, template: dict[str, Any], message: NotificationMessage
    ) -> dict[str, Any]:
        """Apply message values to a template.

        Supports simple {field} substitutions in string values.
        """
        substitutions = {
            "title": message.title,
            "message": message.message,
            "severity": message.severity,
            "channel": message.channel,
        }

        def substitute(value: Any) -> Any:
            if isinstance(value, str):
                result = value
                for key, sub_value in substitutions.items():
                    result = result.replace(f"{{{key}}}", str(sub_value))
                return result
            elif isinstance(value, dict):
                return {k: substitute(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [substitute(v) for v in value]
            return value

        return substitute(template)

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        """Send a generic HTTP notification.

        Args:
            message: The notification message
            config: Custom configuration with endpoint

        Returns:
            Result dictionary with success status
        """
        if not self.validate_config(config):
            return {
                "success": False,
                "error": "Invalid configuration (endpoint or webhook_url required)",
            }

        endpoint = config.get("endpoint") or config.get("webhook_url")
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        timeout = config.get("timeout", 30)

        try:
            # Build the payload
            payload = self._build_payload(message, config)
            payload_json = json.dumps(payload).encode("utf-8")

            # Default headers
            request_headers = {
                "Content-Type": "application/json",
                "User-Agent": "ServerMaintenance/1.0",
            }
            # Add custom headers
            request_headers.update(headers)

            # Create request
            request = urllib.request.Request(
                endpoint,
                data=payload_json if method in ("POST", "PUT", "PATCH") else None,
                headers=request_headers,
                method=method,
            )

            # Send request
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read().decode("utf-8")
                status_code = response.getcode()

                # Try to parse response as JSON
                try:
                    response_data = json.loads(response_body)
                except json.JSONDecodeError:
                    response_data = {"raw": response_body}

                logger.info(f"Generic notification sent to {endpoint}: {status_code}")

                return {
                    "success": True,
                    "message_id": f"generic_{hash(endpoint + message.title) & 0x7FFFFFFF:08x}",
                    "metadata": {
                        "endpoint": endpoint,
                        "method": method,
                        "status_code": status_code,
                        "response": response_data,
                    },
                }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            logger.error(f"Generic HTTP error {e.code}: {error_body}")
            return {
                "success": False,
                "error": f"HTTP error ({e.code}): {error_body}",
            }
        except urllib.error.URLError as e:
            logger.error(f"Generic URL error: {e.reason}")
            return {
                "success": False,
                "error": f"Failed to connect: {e.reason}",
            }
        except Exception as e:
            logger.exception(f"Failed to send generic notification: {e}")
            return {
                "success": False,
                "error": f"Failed to send notification: {e}",
            }
