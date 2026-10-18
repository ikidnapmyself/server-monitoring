"""PagerDuty notification driver."""

import json
import logging
import urllib.error
from typing import Any
from urllib.request import Request  # noqa: TID251 — Request is a data object, not urlopen

from django.conf import settings

from apps.notify.config_fields import ConfigField, timeout_field
from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage
from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError

logger = logging.getLogger(__name__)


class PagerDutyNotifyDriver(BaseNotifyDriver):
    """Driver for sending PagerDuty notifications via Events API v2."""

    name = "pagerduty"
    label = "PagerDuty"
    config_fields = (
        ConfigField(
            "integration_key",
            "Integration key",
            "text",
            required=True,
            secret=True,
            help="The Events API v2 routing key of the PagerDuty service.",
        ),
        ConfigField("client", "Client name", "text", help="Shown in PagerDuty as the sender."),
        ConfigField("client_url", "Client URL", "url"),
        timeout_field(),
    )

    EVENTS_API_URL = "https://events.pagerduty.com/v2/enqueue"

    SEVERITY_MAP = {
        "critical": "critical",
        "warning": "warning",
        "info": "info",
        "success": "info",
    }

    def validate_config(self, config: dict[str, Any]) -> bool:
        if "integration_key" not in config:
            return False
        # Integration key should be 32 characters
        key = config["integration_key"]
        return isinstance(key, str) and len(key) >= 20

    def _build_payload(
        self, message: NotificationMessage, config: dict[str, Any]
    ) -> dict[str, Any]:
        event_action = config.get("event_action", "trigger")

        payload: dict[str, Any] = {
            "routing_key": config["integration_key"],
            "event_action": event_action,
        }

        if config.get("dedup_key"):
            payload["dedup_key"] = config["dedup_key"]
        elif message.tags.get("fingerprint"):
            payload["dedup_key"] = message.tags["fingerprint"]

        if event_action == "trigger":
            prepared = self._prepare_notification(message, config)

            # Template must provide the payload structure
            if prepared.get("payload_obj"):
                payload["payload"] = prepared["payload_obj"]
                # Map severity to PagerDuty's expected values
                if "severity" in payload["payload"]:
                    payload["payload"]["severity"] = self.SEVERITY_MAP.get(
                        payload["payload"]["severity"], "info"
                    )
            else:
                raise ValueError("PagerDuty payload template required but not rendered")

            if config.get("client"):
                payload["client"] = config["client"]
            if config.get("client_url"):
                payload["client_url"] = config["client_url"]

            if message.context.get("console_url"):
                payload["links"] = [
                    {"href": message.context["console_url"], "text": "Open in the console"}
                ]

        return payload

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        if not self.validate_config(config):
            return {
                "success": False,
                "error": "Invalid PagerDuty configuration (valid integration_key required)",
            }

        timeout = config.get("timeout", 30)

        try:
            payload = self._build_payload(message, config)
            payload_json = json.dumps(payload).encode("utf-8")

            request = Request(
                self.EVENTS_API_URL,
                data=payload_json,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with safe_urlopen(
                request, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                response_data = json.loads(response_body)

                if response_data.get("status") == "success":
                    dedup_key = response_data.get("dedup_key", "")
                    logger.info(f"PagerDuty event sent: {message.title} (dedup_key: {dedup_key})")
                    return {
                        "success": True,
                        "message_id": dedup_key,
                        "metadata": {
                            "dedup_key": dedup_key,
                            "event_action": payload.get("event_action"),
                            "severity": self.SEVERITY_MAP.get(message.severity),
                            "message": response_data.get("message", ""),
                        },
                    }
                else:
                    error_msg = response_data.get("message", "Unknown error")
                    logger.warning(f"PagerDuty error: {error_msg}")
                    return {
                        "success": False,
                        "error": f"PagerDuty error: {error_msg}",
                    }

        except URLNotAllowedError:
            return {
                "success": False,
                "error": "PagerDuty URL not allowed by security policy",
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            try:
                error_data = json.loads(error_body)
                error_msg = error_data.get("message", error_body)
            except json.JSONDecodeError:
                error_msg = error_body
            logger.error(f"PagerDuty HTTP error {e.code}: {error_msg}")
            return {
                "success": False,
                "error": f"PagerDuty API error ({e.code}): {error_msg}",
                "status_code": e.code,
            }
        except urllib.error.URLError as e:
            return self._handle_url_error(e, "PagerDuty")
        except Exception as e:
            return self._handle_exception(e, "PagerDuty", "send PagerDuty event")

    def acknowledge(self, dedup_key: str, config: dict[str, Any]) -> dict[str, Any]:
        # Create a minimal message for acknowledgment
        message = NotificationMessage(
            title="Acknowledgment",
            message="Incident acknowledged",
            severity="info",
        )
        config_with_action = {
            **config,
            "event_action": "acknowledge",
            "dedup_key": dedup_key,
        }
        return self.send(message, config_with_action)

    def resolve(self, dedup_key: str, config: dict[str, Any]) -> dict[str, Any]:
        # Create a minimal message for resolution
        message = NotificationMessage(
            title="Resolution",
            message="Incident resolved",
            severity="success",
        )
        config_with_action = {
            **config,
            "event_action": "resolve",
            "dedup_key": dedup_key,
        }
        return self.send(message, config_with_action)
