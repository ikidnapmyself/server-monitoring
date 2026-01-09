"""
New Relic driver.

Handles incoming webhooks from New Relic Alerts.
See: https://docs.newrelic.com/docs/alerts-applied-intelligence/notifications/webhook-notification/
"""

from datetime import datetime
from typing import Any

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class NewRelicDriver(BaseAlertDriver):
    """
    Driver for New Relic webhooks.

    New Relic sends alerts in the following format:
    {
        "account_id": 123,
        "account_name": "...",
        "condition_id": 456,
        "condition_name": "...",
        "current_state": "open",
        "details": "...",
        "event_type": "INCIDENT",
        "incident_acknowledge_url": "...",
        "incident_id": 789,
        "incident_url": "...",
        "owner": "...",
        "policy_name": "...",
        "policy_url": "...",
        "runbook_url": "...",
        "severity": "CRITICAL",
        "targets": [...],
        "timestamp": 1234567890,
        "violation_callback_url": "...",
        "violation_chart_url": "..."
    }
    """

    name = "newrelic"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Check if this looks like a New Relic payload."""
        # New Relic-specific keys
        newrelic_keys = {"condition_id", "incident_id", "policy_name", "condition_name"}
        has_newrelic_keys = len(newrelic_keys & set(payload.keys())) >= 2

        # Alternative: check for account_id + current_state
        if "account_id" in payload and "current_state" in payload:
            return True

        # Check for New Relic workflow format
        if "issueUrl" in payload and "accumulations" in payload:
            return True

        return has_newrelic_keys

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse New Relic webhook payload."""
        if not self.validate(payload):
            raise ValueError("Invalid New Relic payload")

        # Check if this is a workflow notification (newer format)
        if "issueUrl" in payload:
            alerts = [self._parse_workflow_alert(payload)]
        else:
            alerts = [self._parse_classic_alert(payload)]

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            external_url=payload.get("incident_url", payload.get("issueUrl", "")),
            raw_payload=payload,
        )

    def _parse_classic_alert(self, payload: dict[str, Any]) -> ParsedAlert:
        """Parse classic New Relic alert format."""
        # Get alert name from condition_name or policy_name
        name = payload.get("condition_name", payload.get("policy_name", "New Relic Alert"))

        # Determine status from current_state
        current_state = payload.get("current_state", "").lower()
        status = "resolved" if current_state in ("closed", "acknowledged") else "firing"

        # Map severity
        severity_map = {
            "critical": "critical",
            "high": "critical",
            "warning": "warning",
            "medium": "warning",
            "low": "info",
            "info": "info",
        }
        severity_str = payload.get("severity", "warning").lower()
        severity = severity_map.get(severity_str, "warning")

        # Build labels
        labels = {
            "alertname": name,
            "account_id": str(payload.get("account_id", "")),
            "account_name": payload.get("account_name", ""),
            "condition_id": str(payload.get("condition_id", "")),
            "policy_name": payload.get("policy_name", ""),
            "incident_id": str(payload.get("incident_id", "")),
        }

        # Add targets to labels
        targets = payload.get("targets", [])
        if targets and isinstance(targets, list):
            for i, target in enumerate(targets[:3]):  # Limit to first 3 targets
                if isinstance(target, dict):
                    labels[f"target_{i}_name"] = target.get("name", "")
                    labels[f"target_{i}_type"] = target.get("type", "")

        # Parse timestamp
        started_at = self._parse_timestamp(payload.get("timestamp"))

        fingerprint = str(payload.get("incident_id", "")) or self.generate_fingerprint(labels, name)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=payload.get("details", ""),
            labels=labels,
            annotations={
                "incident_url": payload.get("incident_url", ""),
                "runbook_url": payload.get("runbook_url", ""),
            },
            started_at=started_at,
            raw_payload=payload,
        )

    def _parse_workflow_alert(self, payload: dict[str, Any]) -> ParsedAlert:
        """Parse New Relic workflow notification format."""
        # Get alert name from title or accumulations
        accumulations = payload.get("accumulations", {})
        name = payload.get(
            "title",
            (
                accumulations.get("conditionName", ["New Relic Alert"])[0]
                if isinstance(accumulations.get("conditionName"), list)
                else "New Relic Alert"
            ),
        )

        # Determine status
        state = payload.get("state", "").lower()
        status = "resolved" if state in ("closed", "acknowledged") else "firing"

        # Get severity
        priority = payload.get("priority", "").lower()
        severity = (
            "critical"
            if priority in ("critical", "high")
            else "warning" if priority == "medium" else "info"
        )

        labels = {
            "alertname": name,
            "issue_id": payload.get("issueId", ""),
        }

        started_at = self._parse_timestamp(payload.get("createdAt"))

        fingerprint = payload.get("issueId") or self.generate_fingerprint(labels, name)

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=payload.get("description", ""),
            labels=labels,
            annotations={"issue_url": payload.get("issueUrl", "")},
            started_at=started_at,
            raw_payload=payload,
        )

    def _parse_timestamp(self, ts: str | int | None) -> datetime:
        """Parse timestamp (Unix timestamp in seconds or milliseconds)."""
        if not ts:
            return datetime.now()
        try:
            if isinstance(ts, int):
                # Handle milliseconds
                if ts > 10000000000:
                    ts = ts // 1000
                return datetime.fromtimestamp(ts)
            if isinstance(ts, str):
                ts = ts.replace("Z", "+00:00")
                return datetime.fromisoformat(ts)
        except (ValueError, TypeError, OSError):
            pass
        return datetime.now()
