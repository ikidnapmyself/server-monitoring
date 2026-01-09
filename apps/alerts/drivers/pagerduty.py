"""
PagerDuty driver.

Handles incoming webhooks from PagerDuty.
See: https://developer.pagerduty.com/docs/webhooks/v3-overview/
"""

from datetime import datetime
from typing import Any

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class PagerDutyDriver(BaseAlertDriver):
    """
    Driver for PagerDuty webhooks (V3).

    PagerDuty sends events in the following format:
    {
        "event": {
            "id": "...",
            "event_type": "incident.triggered",
            "resource_type": "incident",
            "occurred_at": "...",
            "data": {
                "id": "...",
                "type": "incident",
                "self": "...",
                "html_url": "...",
                "number": 123,
                "status": "triggered",
                "title": "...",
                "service": {...},
                "urgency": "high",
                "priority": {...}
            }
        }
    }
    """

    name = "pagerduty"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Check if this looks like a PagerDuty payload."""
        # V3 webhook format
        if "event" in payload:
            event = payload.get("event", {})
            return "event_type" in event and "resource_type" in event

        # V2 webhook format (legacy)
        if "messages" in payload:
            messages = payload.get("messages", [])
            if messages and isinstance(messages, list):
                return "incident" in messages[0] or "type" in messages[0]

        return False

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse PagerDuty webhook payload."""
        if not self.validate(payload):
            raise ValueError("Invalid PagerDuty payload")

        alerts = []

        # V3 webhook format
        if "event" in payload:
            event = payload.get("event", {})
            parsed = self._parse_v3_event(event)
            alerts.append(parsed)

        # V2 webhook format (legacy)
        elif "messages" in payload:
            for message in payload.get("messages", []):
                parsed = self._parse_v2_message(message)
                alerts.append(parsed)

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            raw_payload=payload,
        )

    def _parse_v3_event(self, event: dict[str, Any]) -> ParsedAlert:
        """Parse a V3 event from PagerDuty."""
        data = event.get("data", {})
        event_type = event.get("event_type", "")

        # Determine status from event type
        status = "firing"
        if "resolved" in event_type or "acknowledged" in event_type:
            status = "resolved"

        # Get alert name
        name = data.get("title", "PagerDuty Incident")

        # Get severity from urgency
        urgency = data.get("urgency", "high")
        severity = "critical" if urgency == "high" else "warning"

        # Check priority if available
        priority = data.get("priority", {})
        if priority:
            priority_name = priority.get("name", "").lower()
            if "p1" in priority_name or "critical" in priority_name:
                severity = "critical"
            elif "p3" in priority_name or "low" in priority_name:
                severity = "info"

        # Build labels
        service = data.get("service", {})
        labels = {
            "alertname": name,
            "incident_id": str(data.get("id", "")),
            "incident_number": str(data.get("number", "")),
            "service_id": service.get("id", ""),
            "service_name": service.get("summary", ""),
            "urgency": urgency,
        }

        # Parse timestamp
        started_at = self._parse_timestamp(event.get("occurred_at"))

        fingerprint = data.get("id") or self.generate_fingerprint(labels, name)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=data.get("description", ""),
            labels=labels,
            annotations={"html_url": data.get("html_url", "")},
            started_at=started_at,
            raw_payload=event,
        )

    def _parse_v2_message(self, message: dict[str, Any]) -> ParsedAlert:
        """Parse a V2 message from PagerDuty (legacy format)."""
        incident = message.get("incident", {})
        msg_type = message.get("type", "")

        # Determine status
        status = "firing"
        if "resolve" in msg_type:
            status = "resolved"

        name = incident.get("trigger_summary_data", {}).get(
            "subject", incident.get("title", "PagerDuty Incident")
        )

        # Get severity
        urgency = incident.get("urgency", "high")
        severity = "critical" if urgency == "high" else "warning"

        labels = {
            "alertname": name,
            "incident_id": str(incident.get("id", "")),
            "incident_number": str(incident.get("incident_number", "")),
            "service_name": incident.get("service", {}).get("name", ""),
        }

        started_at = self._parse_timestamp(incident.get("created_on"))

        fingerprint = incident.get("id") or self.generate_fingerprint(labels, name)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=incident.get("description", ""),
            labels=labels,
            started_at=started_at,
            raw_payload=message,
        )

    def _parse_timestamp(self, ts: str | None) -> datetime:
        """Parse ISO 8601 timestamp."""
        if not ts:
            return datetime.now()
        try:
            # Handle various ISO formats
            ts = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return datetime.now()

