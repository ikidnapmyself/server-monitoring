"""
Grafana alerting driver.

Handles incoming webhooks from Grafana Alerting.
See: https://grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/integrations/webhook-notifier/
"""

from datetime import datetime
from typing import Any

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class GrafanaDriver(BaseAlertDriver):
    """
    Driver for Grafana Alerting webhooks.

    Grafana sends alerts in the following format:
    {
        "receiver": "webhook",
        "status": "firing",
        "alerts": [...],
        "groupLabels": {...},
        "commonLabels": {...},
        "commonAnnotations": {...},
        "externalURL": "...",
        "version": "1",
        "groupKey": "...",
        "orgId": 1,
        "title": "...",
        "state": "alerting",
        "message": "..."
    }
    """

    name = "grafana"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Check if this looks like a Grafana payload."""
        # Grafana-specific keys
        grafana_keys = {"orgId", "state", "title"}
        has_grafana_keys = bool(grafana_keys & set(payload.keys()))

        # Also check for alerts array
        has_alerts = "alerts" in payload or "evalMatches" in payload

        return has_grafana_keys or (has_alerts and "dashboardId" in payload)

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse Grafana webhook payload."""
        if not self.validate(payload):
            raise ValueError("Invalid Grafana payload")

        alerts = []

        # Grafana unified alerting format
        if "alerts" in payload:
            for alert_data in payload.get("alerts", []):
                parsed = self._parse_unified_alert(alert_data, payload)
                alerts.append(parsed)
        # Legacy Grafana alerting format
        elif "evalMatches" in payload:
            parsed = self._parse_legacy_alert(payload)
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

    def _parse_unified_alert(
        self, alert_data: dict[str, Any], payload: dict[str, Any]
    ) -> ParsedAlert:
        """Parse a single alert from Grafana unified alerting format."""
        labels = alert_data.get("labels", {})
        annotations = alert_data.get("annotations", {})

        # Get alert name
        name = labels.get("alertname", alert_data.get("alertname", "Unknown Alert"))

        # Get severity
        severity = labels.get("severity", "warning")

        # Generate fingerprint
        fingerprint = alert_data.get("fingerprint")
        if not fingerprint:
            fingerprint = self.generate_fingerprint(labels, name)

        # Parse timestamps
        started_at = self._parse_timestamp(alert_data.get("startsAt"))
        ended_at = None
        if alert_data.get("endsAt"):
            ended_at = self._parse_timestamp(alert_data.get("endsAt"))
            if ended_at and ended_at.year > datetime.now().year + 1:
                ended_at = None

        # Get description
        description = annotations.get("description", "")
        if not description:
            description = annotations.get("summary", alert_data.get("message", ""))

        # Determine status
        status = alert_data.get("status", "firing")

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

    def _parse_legacy_alert(self, payload: dict[str, Any]) -> ParsedAlert:
        """Parse legacy Grafana alerting format."""
        name = payload.get("ruleName", payload.get("title", "Unknown Alert"))

        # Build labels from payload
        labels = {
            "alertname": name,
            "ruleId": str(payload.get("ruleId", "")),
            "dashboardId": str(payload.get("dashboardId", "")),
            "panelId": str(payload.get("panelId", "")),
            "orgId": str(payload.get("orgId", "")),
        }

        # Map Grafana state to status
        state = payload.get("state", "alerting")
        status = "resolved" if state == "ok" else "firing"

        # Determine severity from state
        severity = "warning"
        if state in ("alerting", "critical"):
            severity = "critical"

        fingerprint = self.generate_fingerprint(labels, name)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=payload.get("message", ""),
            labels=labels,
            annotations={"ruleUrl": payload.get("ruleUrl", "")},
            started_at=datetime.now(),
            ended_at=None if status == "firing" else datetime.now(),
            raw_payload=payload,
        )

    def _parse_timestamp(self, timestamp_str: str | None) -> datetime:
        """Parse timestamp from Grafana format."""
        if not timestamp_str:
            return datetime.now()

        try:
            from django.utils.dateparse import parse_datetime

            parsed = parse_datetime(timestamp_str)
            if parsed:
                return parsed

            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return datetime.now()
