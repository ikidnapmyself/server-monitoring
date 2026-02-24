"""
Prometheus AlertManager driver.

Handles incoming webhooks from Prometheus AlertManager.
See: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
"""

from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class AlertManagerDriver(BaseAlertDriver):
    """
    Driver for Prometheus AlertManager webhooks.

    AlertManager sends alerts in the following format:
    {
        "version": "4",
        "groupKey": "...",
        "receiver": "webhook",
        "status": "firing",
        "alerts": [...],
        "groupLabels": {...},
        "commonLabels": {...},
        "commonAnnotations": {...},
        "externalURL": "..."
    }
    """

    name = "alertmanager"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Check if this looks like an AlertManager payload."""
        # AlertManager payloads have these keys
        required_keys = {"alerts", "status"}
        has_required = required_keys.issubset(payload.keys())

        # Check for AlertManager-specific keys
        am_keys = {"groupKey", "receiver", "groupLabels", "commonLabels"}
        has_am_keys = bool(am_keys & set(payload.keys()))

        return has_required and has_am_keys

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse AlertManager webhook payload."""
        if not self.validate(payload):
            raise ValueError("Invalid AlertManager payload")

        alerts = []
        for alert_data in payload.get("alerts", []):
            parsed = self._parse_alert(alert_data)
            alerts.append(parsed)

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            version=payload.get("version", ""),
            group_key=payload.get("groupKey", ""),
            receiver=payload.get("receiver", ""),
            external_url=payload.get("externalURL", ""),
            raw_payload=payload,
        )

    def _parse_alert(self, alert_data: dict[str, Any]) -> ParsedAlert:
        """Parse a single alert from AlertManager format."""
        labels = alert_data.get("labels", {})
        annotations = alert_data.get("annotations", {})

        # Get alert name from labels (AlertManager convention)
        name = labels.get("alertname", "Unknown Alert")

        # Get severity from labels (common convention)
        severity = labels.get("severity", "warning")

        # Use AlertManager's fingerprint if available
        fingerprint = alert_data.get("fingerprint")
        if not fingerprint:
            fingerprint = self.generate_fingerprint(labels, name)

        # Parse timestamps
        started_at = self._parse_timestamp(alert_data.get("startsAt"))
        ended_at = None
        if alert_data.get("endsAt"):
            ended_at = self._parse_timestamp(alert_data.get("endsAt"))
            # AlertManager sets endsAt to a far future date for firing alerts
            if ended_at and ended_at.year > timezone.now().year + 1:
                ended_at = None

        # Get description from annotations
        description = annotations.get("description", "")
        if not description:
            description = annotations.get("summary", "")

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=alert_data.get("status", "firing"),
            severity=severity,
            description=description,
            labels=labels,
            annotations=annotations,
            started_at=started_at,
            ended_at=ended_at,
            raw_payload=alert_data,
        )

    def _parse_timestamp(self, timestamp_str: str | None) -> datetime:
        """Parse AlertManager timestamp format (RFC3339)."""
        if not timestamp_str:
            return timezone.now()

        try:
            # Handle RFC3339 format with timezone
            # Example: "2024-01-08T10:30:00.000Z" or "2024-01-08T10:30:00+00:00"
            from django.utils.dateparse import parse_datetime

            parsed = parse_datetime(timestamp_str)
            if parsed:
                return parsed

            # Fallback: try ISO format
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return timezone.now()
