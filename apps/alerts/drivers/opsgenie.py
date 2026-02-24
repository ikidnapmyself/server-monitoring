"""
OpsGenie driver.

Handles incoming webhooks from OpsGenie.
See: https://support.atlassian.com/opsgenie/docs/integrate-with-webhook/
"""

from datetime import datetime
from datetime import timezone as dt_tz
from typing import Any

from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class OpsGenieDriver(BaseAlertDriver):
    """
    Driver for OpsGenie webhooks.

    OpsGenie sends alerts in the following format:
    {
        "action": "Create",
        "alert": {
            "alertId": "...",
            "message": "...",
            "tags": [...],
            "tinyId": "...",
            "entity": "...",
            "alias": "...",
            "createdAt": 1234567890,
            "updatedAt": 1234567890,
            "username": "...",
            "userId": "...",
            "description": "...",
            "team": "...",
            "source": "...",
            "priority": "P1"
        },
        "source": {...},
        "integrationId": "...",
        "integrationName": "..."
    }
    """

    name = "opsgenie"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Check if this looks like an OpsGenie payload."""
        # OpsGenie-specific structure
        if "alert" in payload and "action" in payload:
            alert = payload.get("alert", {})
            return "alertId" in alert or "tinyId" in alert

        # Alternative: check for integrationId/integrationName
        if "integrationId" in payload and "integrationName" in payload:
            return True

        return False

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse OpsGenie webhook payload."""
        if not self.validate(payload):
            raise ValueError("Invalid OpsGenie payload")

        alerts = [self._parse_alert(payload)]

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            raw_payload=payload,
        )

    def _parse_alert(self, payload: dict[str, Any]) -> ParsedAlert:
        """Parse an OpsGenie alert payload."""
        alert_data = payload.get("alert", {})
        action = payload.get("action", "").lower()

        # Get alert name
        name = alert_data.get("message", "OpsGenie Alert")

        # Determine status from action
        status = "firing"
        resolved_actions = {"close", "acknowledge", "ack", "resolve", "delete"}
        if action in resolved_actions:
            status = "resolved"

        # Map priority to severity
        priority = alert_data.get("priority", "P3").upper()
        priority_map = {
            "P1": "critical",
            "P2": "critical",
            "P3": "warning",
            "P4": "info",
            "P5": "info",
        }
        severity = priority_map.get(priority, "warning")

        # Parse tags into labels
        labels = {"alertname": name}
        tags = alert_data.get("tags", [])
        if tags and isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str):
                    if ":" in tag:
                        key, value = tag.split(":", 1)
                        labels[key] = value
                    else:
                        labels[f"tag_{tag}"] = "true"

        # Add common fields
        labels["alert_id"] = alert_data.get("alertId", "")
        labels["tiny_id"] = alert_data.get("tinyId", "")
        labels["priority"] = priority
        if alert_data.get("entity"):
            labels["entity"] = alert_data["entity"]
        if alert_data.get("alias"):
            labels["alias"] = alert_data["alias"]
        if alert_data.get("team"):
            labels["team"] = alert_data["team"]
        if alert_data.get("source"):
            labels["source"] = alert_data["source"]

        # Parse timestamp
        started_at = self._parse_timestamp(alert_data.get("createdAt"))

        # Generate fingerprint
        fingerprint = alert_data.get("alertId") or alert_data.get("alias")
        if not fingerprint:
            fingerprint = self.generate_fingerprint(labels, name)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=alert_data.get("description", ""),
            labels=labels,
            annotations={
                "action": action,
                "username": alert_data.get("username", ""),
            },
            started_at=started_at,
            raw_payload=payload,
        )

    def _parse_timestamp(self, ts: int | None) -> datetime:
        """Parse Unix timestamp in milliseconds."""
        if not ts:
            return timezone.now()
        try:
            # OpsGenie uses milliseconds
            if ts > 10000000000:
                ts = ts // 1000
            return datetime.fromtimestamp(ts, tz=dt_tz.utc)
        except (ValueError, TypeError, OSError):
            return timezone.now()
