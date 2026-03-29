"""
Cluster driver for multi-instance deployments.

Parses alert payloads from sibling instances (agents) that push their
check results to a hub via the existing webhook endpoint.

Payload format:
{
    "source": "cluster",
    "instance_id": "web-server-03",
    "hostname": "ip-10-0-1-42",
    "version": "1.0",
    "alerts": [
        {
            "fingerprint": "cpu-check-ip-10-0-1-42",
            "name": "CPU usage critical",
            "status": "firing",
            "severity": "critical",
            "started_at": "2026-03-29T12:00:00Z",
            "labels": {"checker": "cpu", "hostname": "ip-10-0-1-42"},
            "annotations": {"message": "CPU at 95.2%"},
            "metrics": {"cpu_percent": 95.2}
        }
    ]
}
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class ClusterDriver(BaseAlertDriver):
    """Driver for alerts from sibling server-monitoring instances."""

    name = "cluster"
    signature_header = "X-Cluster-Signature"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Validate that this payload is from a cluster agent."""
        return (
            payload.get("source") == "cluster"
            and bool(payload.get("instance_id"))
            and isinstance(payload.get("alerts"), list)
        )

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse cluster agent payload into normalized format."""
        instance_id = payload.get("instance_id", "")
        hostname = payload.get("hostname", "")
        alerts = []

        for alert_data in payload.get("alerts", []):
            parsed = self._parse_alert(alert_data, instance_id, hostname)
            alerts.append(parsed)

        return ParsedPayload(
            alerts=alerts,
            source=self.name,
            version=payload.get("version", ""),
            raw_payload=payload,
        )

    def _parse_alert(
        self,
        alert_data: dict[str, Any],
        instance_id: str,
        hostname: str,
    ) -> ParsedAlert:
        """Parse a single alert from cluster payload."""
        name = alert_data.get("name", "Unknown Alert")
        status = str(alert_data.get("status", "firing")).lower()
        severity = str(alert_data.get("severity", "warning")).lower()

        # Merge labels — always inject instance_id and hostname
        labels = alert_data.get("labels", {})
        if not isinstance(labels, dict):
            labels = {}
        labels = {str(k): str(v) for k, v in labels.items()}
        labels["instance_id"] = instance_id
        if hostname:
            labels["hostname"] = hostname

        # Fingerprint: use provided or generate
        fingerprint = alert_data.get("fingerprint", "")
        if not fingerprint:
            fingerprint = self.generate_fingerprint(labels, name)

        # Annotations — preserve metrics if present
        annotations = alert_data.get("annotations", {})
        if not isinstance(annotations, dict):
            annotations = {}
        metrics = alert_data.get("metrics")
        if metrics:
            annotations["metrics"] = json.dumps(metrics)

        # Timestamps
        started_at = self._parse_timestamp(alert_data.get("started_at"))
        ended_at = None
        if status == "resolved":
            ended_at = self._parse_timestamp(alert_data.get("ended_at"))

        return ParsedAlert(
            fingerprint=fingerprint,
            name=name,
            status=status,
            severity=severity,
            description=alert_data.get("description", ""),
            labels=labels,
            annotations=annotations,
            started_at=started_at,
            ended_at=ended_at,
            raw_payload=alert_data,
        )

    def _parse_timestamp(self, value: Any) -> datetime:
        """Parse a timestamp from string or return now."""
        if not value:
            return timezone.now()
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        return timezone.now()
