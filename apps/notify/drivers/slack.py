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

    This driver is intentionally minimal: the message body and full payload
    should be produced by templates (e.g. `slack_text.j2` or a configured
    `payload_template`). The driver will try to parse the rendered template
    as JSON and send that as the webhook payload; if rendering is plain text
    it will send `{"text": rendered}`.
    """

    name = "slack"

    # Severity to color mapping (kept for potential template use)
    COLOR_MAP = {
        "critical": "#dc3545",
        "warning": "#ffc107",
        "info": "#17a2b8",
        "success": "#28a745",
    }

    # Severity to emoji mapping (kept for template use)
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
        url = config["webhook_url"]
        return isinstance(url, str) and url.startswith("https://hooks.slack.com/")

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        """Send a Slack notification.

        The template (driver-specific template files or a configured
        `payload_template`) must produce either a JSON object (as a string)
        representing the full webhook payload, or a plain text body.

        If JSON is produced, it is sent as-is. If plain text is produced,
        it will be sent as `{"text": "..."}`.
        """
        if not self.validate_config(config):
            return {
                "success": False,
                "error": "Invalid Slack configuration (valid webhook_url required)",
            }

        webhook_url = config["webhook_url"]
        timeout = config.get("timeout", 30)

        try:
            # Render driver templates (this will load driver-specific files such as
            # file:slack_text.j2 or file:slack_payload.j2 when present).
            rendered = self._render_message_templates(message, config)
            rendered_text = rendered.get("text") or ""

            if not rendered_text:
                # If template produced nothing, use fallback message
                rendered_text = message.message or ""

            payload_obj = None
            payload_raw = None

            # If the rendered output looks like JSON, attempt to parse it so
            # templates can produce full payloads (blocks/attachments/etc.).
            stripped = rendered_text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    payload_obj = json.loads(rendered_text)
                except Exception:
                    # Not valid JSON; treat as plain text below
                    payload_obj = None
                    payload_raw = rendered_text
            else:
                payload_raw = rendered_text

            if isinstance(payload_obj, dict):
                payload = payload_obj
            elif isinstance(payload_obj, list):
                # Slack expects an object; wrap list into a `blocks` or `attachments`
                # depending on what the template author intended. We'll wrap into
                # `blocks` by default so templates can output a raw array of blocks.
                payload = {"blocks": payload_obj}
            else:
                # Plain text fallback: send as `text`; templates control the entire
                # body so driver does not mutate other fields.
                payload = {"text": payload_raw}

            # The template is expected to produce a complete payload (including
            # channel/username/icon_emoji if desired). Do not mutate the body
            # here so templates remain authoritative for message content.

            payload_json = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            request = urllib.request.Request(
                webhook_url,
                data=payload_json,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read().decode("utf-8")

                if response_body == "ok":
                    logger.info(f"Slack notification sent: {message.title}")
                    return {
                        "success": True,
                        "message_id": f"slack_{hash(message.title + message.message) & 0x7FFFFFFF:08x}",
                        "metadata": {
                            "channel": payload.get("channel", "default"),
                            "severity": message.severity,
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
            return {"success": False, "error": f"Slack API error ({e.code}): {error_body}"}
        except urllib.error.URLError as e:
            logger.error(f"Slack URL error: {e.reason}")
            return {"success": False, "error": f"Failed to connect to Slack: {e.reason}"}
        except Exception as e:
            logger.exception(f"Failed to send Slack notification: {e}")
            return {"success": False, "error": f"Failed to send Slack notification: {e}"}
