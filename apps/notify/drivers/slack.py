"""Slack notification driver."""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

logger = logging.getLogger(__name__)


class SlackNotifyDriver(BaseNotifyDriver):
    """
    Driver for sending Slack notifications.

    Configuration:
    {
        "webhook_url": "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXX",
        "channel": "#alerts",
        "username": "AlertBot",
        "icon_emoji": ":warning:",
        "timeout": 30
    }
    """

    name = "slack"

    # Severity to color mapping
    COLOR_MAP = {
        "critical": "#dc3545",  # red
        "warning": "#ffc107",  # orange/yellow
        "info": "#17a2b8",  # blue
        "success": "#28a745",  # green
    }

    # Severity to emoji mapping
    EMOJI_MAP = {
        "critical": ":rotating_light:",
        "warning": ":warning:",
        "info": ":information_source:",
        "success": ":white_check_mark:",
    }

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate Slack configuration."""
        if "webhook_url" not in config:
            return False
        # Basic URL validation
        url = config["webhook_url"]
        return url.startswith("https://hooks.slack.com/")

    def _build_payload(
        self, message: NotificationMessage, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Build Slack message payload with attachments."""
        color = self.COLOR_MAP.get(message.severity, "#6c757d")
        emoji = self.EMOJI_MAP.get(message.severity, ":bell:")

        # Build attachment fields from tags and context
        fields = []

        if message.tags:
            for key, value in message.tags.items():
                fields.append(
                    {
                        "title": key.replace("_", " ").title(),
                        "value": str(value),
                        "short": True,
                    }
                )

        if message.context:
            for key, value in message.context.items():
                fields.append(
                    {
                        "title": key.replace("_", " ").title(),
                        "value": str(value),
                        "short": True,
                    }
                )

        # Build the attachment
        attachment = {
            "color": color,
            "title": f"{emoji} {message.title}",
            "text": message.message,
            "fields": fields,
            "footer": f"Severity: {message.severity.upper()}",
            "mrkdwn_in": ["text", "title"],
        }

        payload: dict[str, Any] = {
            "attachments": [attachment],
        }

        # Optional overrides
        if config.get("channel"):
            payload["channel"] = config["channel"]
        elif message.channel != "default":
            payload["channel"] = message.channel

        if config.get("username"):
            payload["username"] = config["username"]

        if config.get("icon_emoji"):
            payload["icon_emoji"] = config["icon_emoji"]

        return payload

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        """Send a Slack notification.

        Args:
            message: The notification message
            config: Slack configuration with webhook_url

        Returns:
            Result dictionary with success status
        """
        if not self.validate_config(config):
            return {
                "success": False,
                "error": "Invalid Slack configuration (valid webhook_url required)",
            }

        webhook_url = config["webhook_url"]
        timeout = config.get("timeout", 30)

        try:
            # Build the payload
            payload = self._build_payload(message, config)
            payload_json = json.dumps(payload).encode("utf-8")

            # Create request
            request = urllib.request.Request(
                webhook_url,
                data=payload_json,
                headers={
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            # Send request
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read().decode("utf-8")

                # Slack returns "ok" for successful webhooks
                if response_body == "ok":
                    logger.info(f"Slack notification sent: {message.title}")
                    return {
                        "success": True,
                        "message_id": f"slack_{hash(message.title + message.message) & 0x7FFFFFFF:08x}",
                        "metadata": {
                            "channel": payload.get("channel", "default"),
                            "severity": message.severity,
                            "color": self.COLOR_MAP.get(message.severity),
                        },
                    }
                else:
                    logger.warning(f"Unexpected Slack response: {response_body}")
                    return {
                        "success": False,
                        "error": f"Unexpected Slack response: {response_body}",
                    }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            logger.error(f"Slack HTTP error {e.code}: {error_body}")
            return {
                "success": False,
                "error": f"Slack API error ({e.code}): {error_body}",
            }
        except urllib.error.URLError as e:
            logger.error(f"Slack URL error: {e.reason}")
            return {
                "success": False,
                "error": f"Failed to connect to Slack: {e.reason}",
            }
        except Exception as e:
            logger.exception(f"Failed to send Slack notification: {e}")
            return {
                "success": False,
                "error": f"Failed to send Slack notification: {e}",
            }
