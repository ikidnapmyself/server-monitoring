"""
Zabbix driver.

Handles incoming webhooks from Zabbix.
See: https://www.zabbix.com/documentation/current/en/manual/config/notifications/media/webhook
"""

from datetime import datetime
from typing import Any

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class ZabbixDriver(BaseAlertDriver):
    """
    Driver for Zabbix webhooks.

    Zabbix webhook payloads are customizable, but commonly include:
    {
        "event_id": "...",
        "event_name": "...",
        "event_source": "...",
        "event_severity": "...",
        "event_status": "...",
        "event_value": "...",
        "host_name": "...",
        "host_ip": "...",
        "trigger_id": "...",
        "trigger_name": "...",
        "trigger_severity": "...",
        "trigger_status": "...",
        "item_name": "...",
        "item_value": "...",
        "alert_message": "...",
        "event_date": "...",
        "event_time": "..."
    }
    """

    name = "zabbix"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Check if this looks like a Zabbix payload."""
        # Zabbix-specific keys
        zabbix_keys = {"event_id", "trigger_id", "trigger_name", "trigger_severity", "host_name"}
        has_zabbix_keys = len(zabbix_keys & set(payload.keys())) >= 2

        # Alternative check for event-based format
        if "event_source" in payload and "event_value" in payload:
            return True

        return has_zabbix_keys

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse Zabbix webhook payload."""
        if not self.validate(payload):
            raise ValueError("Invalid Zabbix payload")

        alerts = [self._parse_alert(payload)]

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            external_url=payload.get("zabbix_url", ""),
            raw_payload=payload,
        )

    def _parse_alert(self, payload: dict[str, Any]) -> ParsedAlert:
        """Parse a Zabbix alert payload."""
        # Get alert name from trigger_name or event_name
        name = payload.get("trigger_name", payload.get("event_name", "Zabbix Alert"))

        # Determine status from event_value or trigger_status or event_status
        # Zabbix: 1 = PROBLEM, 0 = OK
        event_value = payload.get("event_value", "")
        trigger_status = payload.get("trigger_status", "").upper()
        event_status = payload.get("event_status", "").upper()

        status = "firing"
        if event_value == "0" or trigger_status == "OK" or event_status in ("RESOLVED", "OK"):
            status = "resolved"
        elif event_status == "PROBLEM" or trigger_status == "PROBLEM" or event_value == "1":
            status = "firing"

        # Map severity
        severity_str = payload.get("trigger_severity", payload.get("event_severity", "")).lower()
        severity_map = {
            "disaster": "critical",
            "high": "critical",
            "average": "warning",
            "warning": "warning",
            "information": "info",
            "not classified": "info",
            # Numeric severities (Zabbix uses 0-5)
            "5": "critical",
            "4": "critical",
            "3": "warning",
            "2": "warning",
            "1": "info",
            "0": "info",
        }
        severity = severity_map.get(severity_str, "warning")

        # Build labels
        labels = {
            "alertname": name,
            "host_name": payload.get("host_name", ""),
            "host_ip": payload.get("host_ip", ""),
            "trigger_id": str(payload.get("trigger_id", "")),
            "event_id": str(payload.get("event_id", "")),
        }

        # Add optional fields
        if payload.get("item_name"):
            labels["item_name"] = payload["item_name"]
        if payload.get("host_group"):
            labels["host_group"] = payload["host_group"]

        # Parse timestamp
        event_date = payload.get("event_date", "")
        event_time = payload.get("event_time", "")
        timestamp_str = f"{event_date} {event_time}".strip() if event_date else None
        started_at = self._parse_timestamp(timestamp_str or payload.get("event_timestamp"))

        # Generate fingerprint
        fingerprint = payload.get("event_id") or payload.get("trigger_id")
        if fingerprint:
            fingerprint = str(fingerprint)
        else:
            fingerprint = self.generate_fingerprint(labels, name)

        # Build description
        description = payload.get("alert_message", payload.get("event_message", ""))
        if not description and payload.get("item_name") and payload.get("item_value"):
            description = f"{payload['item_name']}: {payload['item_value']}"

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=description,
            labels=labels,
            annotations={
                "item_value": str(payload.get("item_value", "")),
                "zabbix_url": payload.get("zabbix_url", ""),
            },
            started_at=started_at,
            raw_payload=payload,
        )

    def _parse_timestamp(self, ts: str | int | None) -> datetime:
        """Parse timestamp (various formats)."""
        if not ts:
            return datetime.now()
        try:
            if isinstance(ts, int):
                return datetime.fromtimestamp(ts)
            if isinstance(ts, str):
                # Try common Zabbix date formats
                for fmt in [
                    "%Y.%m.%d %H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                    "%d.%m.%Y %H:%M:%S",
                ]:
                    try:
                        return datetime.strptime(ts, fmt)
                    except ValueError:
                        continue
                # Try ISO format
                ts = ts.replace("Z", "+00:00")
                return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            pass
        return datetime.now()

