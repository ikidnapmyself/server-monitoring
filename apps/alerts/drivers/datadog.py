"""
Datadog driver.

Handles incoming webhooks from Datadog.
See: https://docs.datadoghq.com/integrations/webhooks/
"""

from datetime import datetime
from datetime import timezone as dt_tz
from typing import Any

from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class DatadogDriver(BaseAlertDriver):
    """
    Driver for Datadog webhooks.

    Datadog sends alerts in the following format:
    {
        "id": "...",
        "title": "...",
        "last_updated": "...",
        "event_type": "...",
        "alert_id": "...",
        "alert_metric": "...",
        "alert_status": "...",
        "alert_title": "...",
        "alert_transition": "...",
        "alert_type": "...",
        "event_msg": "...",
        "hostname": "...",
        "org": {...},
        "priority": "...",
        "tags": "...",
        "url": "..."
    }
    """

    name = "datadog"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Check if this looks like a Datadog payload."""
        # Datadog-specific keys
        datadog_keys = {"alert_id", "alert_status", "alert_type", "alert_transition"}
        has_datadog_keys = bool(datadog_keys & set(payload.keys()))

        # Alternative: check for org.name or org.id (Datadog org info)
        if "org" in payload and isinstance(payload.get("org"), dict):
            return True

        return has_datadog_keys

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse Datadog webhook payload."""
        if not self.validate(payload):
            raise ValueError("Invalid Datadog payload")

        alerts = [self._parse_alert(payload)]

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            external_url=payload.get("url", ""),
            raw_payload=payload,
        )

    def _parse_alert(self, payload: dict[str, Any]) -> ParsedAlert:
        """Parse a Datadog alert payload."""
        # Get alert name
        name = payload.get("alert_title", payload.get("title", "Datadog Alert"))

        # Determine status from alert_transition
        transition = payload.get("alert_transition", "").lower()
        alert_status = payload.get("alert_status", "").lower()

        status = "firing"
        if transition in ("recovered", "resolved") or alert_status in ("ok", "recovered"):
            status = "resolved"

        # Determine severity from alert_type and priority
        alert_type = payload.get("alert_type", "").lower()
        priority = payload.get("priority", "").lower()

        severity = "warning"
        if alert_type == "error" or priority in ("p1", "high", "critical"):
            severity = "critical"
        elif priority in ("p3", "p4", "low", "info"):
            severity = "info"

        # Parse tags into labels
        tags_str = payload.get("tags", "")
        labels = {"alertname": name}

        if tags_str:
            if isinstance(tags_str, str):
                for tag in tags_str.split(","):
                    tag = tag.strip()
                    if ":" in tag:
                        key, value = tag.split(":", 1)
                        labels[key] = value
                    else:
                        labels[tag] = "true"
            elif isinstance(tags_str, list):
                for tag in tags_str:
                    if ":" in tag:
                        key, value = tag.split(":", 1)
                        labels[key] = value
                    else:
                        labels[tag] = "true"

        # Add common fields
        if payload.get("hostname"):
            labels["hostname"] = payload["hostname"]
        if payload.get("alert_metric"):
            labels["metric"] = payload["alert_metric"]
        if payload.get("alert_id"):
            labels["alert_id"] = str(payload["alert_id"])

        # Parse timestamp
        started_at = self._parse_timestamp(payload.get("last_updated"))

        # Generate fingerprint
        fingerprint = payload.get("alert_id") or payload.get("id")
        if fingerprint:
            fingerprint = str(fingerprint)
        else:
            fingerprint = self.generate_fingerprint(labels, name)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=payload.get("event_msg", payload.get("body", "")),
            labels=labels,
            annotations={"url": payload.get("url", "")},
            started_at=started_at,
            raw_payload=payload,
        )

    def _parse_timestamp(self, ts: str | int | None) -> datetime:
        """Parse timestamp (can be ISO string or Unix timestamp)."""
        if not ts:
            return timezone.now()
        try:
            if isinstance(ts, int):
                return datetime.fromtimestamp(ts, tz=dt_tz.utc)
            if isinstance(ts, str):
                # Try ISO format
                ts = ts.replace("Z", "+00:00")
                return datetime.fromisoformat(ts)
        except (ValueError, TypeError, OSError):
            pass
        return timezone.now()
