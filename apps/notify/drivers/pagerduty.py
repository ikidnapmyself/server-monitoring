"""PagerDuty notification driver."""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

logger = logging.getLogger(__name__)


class PagerDutyNotifyDriver(BaseNotifyDriver):
    """Driver for sending PagerDuty notifications via Events API v2."""

    name = "pagerduty"

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
        severity = self.SEVERITY_MAP.get(message.severity, "info")
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
            incident = self._compose_incident_details(message, config or {})

            payload["payload"] = {
                "summary": f"[{message.severity.upper()}] {message.title}",
                "severity": severity,
                "source": incident.get("source")
                or message.tags.get("source", "server-maintenance"),
                "component": message.tags.get("component", message.channel),
                "group": message.tags.get("group", "default"),
                "class": message.tags.get("class", message.severity),
                "custom_details": {
                    "message": message.message,
                    "severity": message.severity,
                    "cpu_count": incident.get("cpu_count"),
                    "ram_total_human": incident.get("ram_total_human"),
                    "disk_total_human": incident.get("disk_total_human"),
                    **message.tags,
                    **message.context,
                },
            }

            if config.get("client"):
                payload["client"] = config["client"]
            if config.get("client_url"):
                payload["client_url"] = config["client_url"]

            if message.context.get("url"):
                payload["links"] = [
                    {
                        "href": message.context["url"],
                        "text": "View Details",
                    }
                ]

            prepared = self._prepare_notification(message, config)

            if prepared.get("payload_obj") and isinstance(prepared.get("payload_obj"), dict):
                payload["payload"]["custom_details"].update(prepared.get("payload_obj"))
            elif prepared.get("payload_raw"):
                payload["payload"]["custom_details"]["rendered"] = prepared.get("payload_raw")

            payload["incident"] = prepared.get("incident")

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

            request = urllib.request.Request(
                self.EVENTS_API_URL,
                data=payload_json,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=timeout) as response:
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

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            try:
                error_data = json.loads(error_body)
                error_msg = error_data.get("message", error_body)
            except json.JSONDecodeError:
                error_msg = error_body
            return {"success": False, "error": f"PagerDuty API error ({e.code}): {error_msg}"}
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
