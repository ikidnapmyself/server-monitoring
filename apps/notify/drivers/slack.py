"""Slack notification driver."""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

logger = logging.getLogger(__name__)


class SlackNotifyDriver(BaseNotifyDriver):
    """Driver for sending Slack notifications.

    Uses templates to generate payload (slack_text.j2 or payload_template).
    Sends JSON as-is or plain text as {"text": "..."}.
    """

    name = "slack"

    def validate_config(self, config: dict[str, Any]) -> bool:
        if "webhook_url" not in config:
            return False
        url = config["webhook_url"]
        return isinstance(url, str) and url.startswith("https://hooks.slack.com/")

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        if not self.validate_config(config):
            return {
                "success": False,
                "error": "Invalid Slack configuration (valid webhook_url required)",
            }

        webhook_url = config["webhook_url"]
        timeout = config.get("timeout", 30)

        try:
            rendered = self._render_message_templates(message, config)
            rendered_text = rendered.get("text")

            if not rendered_text:
                raise ValueError("Slack template required but not rendered")

            payload_obj = None
            payload_raw = None

            stripped = rendered_text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    payload_obj = json.loads(rendered_text)
                except Exception:
                    payload_raw = rendered_text
            else:
                payload_raw = rendered_text

            if isinstance(payload_obj, dict):
                payload = payload_obj
            elif isinstance(payload_obj, list):
                payload = {"blocks": payload_obj}
            else:
                payload = {"text": payload_raw}

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
            return self._handle_http_error(e, "Slack")
        except urllib.error.URLError as e:
            return self._handle_url_error(e, "Slack")
        except Exception as e:
            return self._handle_exception(e, "Slack", "send Slack notification")
