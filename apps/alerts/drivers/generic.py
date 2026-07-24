"""
Generic webhook driver.

Handles alerts from custom/generic sources that follow a simple format.
This serves as a fallback and a template for custom integrations.
"""

from datetime import datetime
from datetime import timezone as dt_tz
from typing import Any

from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class GenericWebhookDriver(BaseAlertDriver):
    """
    Driver for generic webhook alerts.

    Accepts a flexible format for custom integrations:
    {
        "alerts": [
            {
                "name": "Alert Name",
                "status": "firing",
                "severity": "warning",
                "description": "...",
                "labels": {...},
                "started_at": "2024-01-08T10:30:00Z"
            }
        ],
        "source": "my-custom-system"
    }

    Or a single alert:
    {
        "name": "Alert Name",
        "status": "firing",
        ...
    }
    """

    name = "generic"

    def validate(self, payload: dict[str, Any]) -> bool:
        """
        Generic driver accepts most payloads as a fallback.

        Validates that either:
        - There's an "alerts" list, or
        - There's a "name" field for a single alert
        """
        has_alerts = "alerts" in payload and isinstance(payload["alerts"], list)
        has_name = "name" in payload or "alert_name" in payload or "title" in payload
        return has_alerts or has_name

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse generic webhook payload."""
        alerts = []

        # Multiple alerts format
        if "alerts" in payload and isinstance(payload["alerts"], list):
            for alert_data in payload["alerts"]:
                parsed = self._parse_alert(alert_data)
                alerts.append(parsed)
        # Single alert format
        else:
            parsed = self._parse_alert(payload)
            alerts.append(parsed)

        return ParsedPayload(
            alerts=alerts,
            source=payload.get("source", self.name),
            version=payload.get("version", ""),
            group_key=payload.get("group_key", ""),
            receiver=payload.get("receiver", ""),
            external_url=payload.get("external_url", ""),
            raw_payload=payload,
        )

    def _parse_alert(self, alert_data: dict[str, Any]) -> ParsedAlert:
        """Parse a single alert from generic format."""
        # Flexible name field lookup
        name = (
            alert_data.get("name")
            or alert_data.get("alert_name")
            or alert_data.get("title")
            or alert_data.get("alertname")
            or "Unknown Alert"
        )

        # Get labels
        labels = alert_data.get("labels", {})
        if isinstance(labels, dict):
            labels = {str(k): str(v) for k, v in labels.items()}
        else:
            labels = {}

        # Generate fingerprint
        fingerprint = alert_data.get("fingerprint")
        if not fingerprint:
            fingerprint = self.generate_fingerprint(labels, name)

        # Parse status
        raw_status = alert_data.get("status")
        raw_state = alert_data.get("state")

        if raw_status is None and raw_state is not None:
            # Support alternative conventions: state=ok/resolved/normal.
            status = str(raw_state).lower()
        else:
            status = str(raw_status or "firing").lower()

        if status not in ("firing", "resolved"):
            # Try to infer from other fields
            state = str(raw_state or "").lower()
            if state in ("ok", "resolved", "normal"):
                status = "resolved"
            else:
                status = "firing"

        # Parse severity
        raw_severity = alert_data.get("severity")
        priority = str(alert_data.get("priority", "") or "").lower()
        level = str(alert_data.get("level", "") or "").lower()

        if raw_severity is None and priority:
            # Support alternative conventions: priority=high/low, etc.
            severity = priority
        else:
            severity = str(raw_severity or "warning").lower()

        if severity not in ("critical", "warning", "info"):
            # Try to infer from priority or level
            if priority in ("high", "critical", "p1") or level in ("error", "critical"):
                severity = "critical"
            elif priority in ("low", "p3", "p4") or level in ("info", "debug"):
                severity = "info"
            else:
                severity = "warning"

        # Parse timestamps
        started_at = self._parse_timestamp(
            alert_data.get("started_at")
            or alert_data.get("startsAt")
            or alert_data.get("timestamp")
            or alert_data.get("time")
        )

        ended_at = None
        if status == "resolved":
            ended_at = self._parse_timestamp(
                alert_data.get("ended_at")
                or alert_data.get("endsAt")
                or alert_data.get("resolved_at")
            )

        # Get description
        description = (
            alert_data.get("description")
            or alert_data.get("message")
            or alert_data.get("summary")
            or alert_data.get("text")
            or ""
        )

        # Get annotations
        annotations = alert_data.get("annotations", {})
        if not isinstance(annotations, dict):
            annotations = {}

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=description,
            labels=labels,
            annotations=annotations,
            started_at=started_at,
            ended_at=ended_at,
            raw_payload=alert_data,
        )

    def _parse_timestamp(self, timestamp: Any) -> datetime:
        """Parse timestamp from various formats."""
        if not timestamp:
            return timezone.now()

        # Already a datetime
        if isinstance(timestamp, datetime):
            return timestamp

        # Unix timestamp (int or float)
        if isinstance(timestamp, (int, float)):
            try:
                # Handle milliseconds
                if timestamp > 1e12:
                    timestamp = timestamp / 1000
                return datetime.fromtimestamp(timestamp, tz=dt_tz.utc)
            except (ValueError, OSError):
                return timezone.now()

        # String timestamp
        if isinstance(timestamp, str):
            try:
                from django.utils.dateparse import parse_datetime

                parsed = parse_datetime(timestamp)
                if parsed:
                    return parsed

                return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        return timezone.now()
