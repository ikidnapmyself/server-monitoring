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
from apps.alerts.identity import checker_fingerprint


def _clean_instance_id(value: Any) -> str:
    """The payload's instance id as a non-blank string, or ``""``.

    Webhook payloads are attacker-controlled, so the value may not be a string at
    all; anything that is not one is rejected rather than coerced.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()


class ClusterDriver(BaseAlertDriver):
    """Driver for alerts from sibling server-monitoring instances.

    Parsing only — this driver makes no routing decision. That node-pushed
    alerts skip the hub's CHECK stage is the ``cluster-nodes`` lane, seeded by
    orchestration migration ``0012``, whose ``description`` explains why.
    """

    name = "cluster"

    def validate(self, payload: dict[str, Any]) -> bool:
        """Validate that this payload is from a cluster agent.

        ``instance_id`` must be a non-blank string: it becomes the checker-alert
        fingerprint and the ``Node`` registry key, and a blank or whitespace-only
        value would silently produce identity for a machine that has no name. Only
        non-blankness is checked — naming is not this driver's business.
        """
        return (
            payload.get("source") == "cluster"
            and bool(_clean_instance_id(payload.get("instance_id")))
            and isinstance(payload.get("alerts"), list)
        )

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse cluster agent payload into normalized format."""
        # Normalised once, here: this value is both the fingerprint input and the
        # label the rest of the pipeline reads as "which machine". A blank one
        # derives ``check::<checker>``, a bucket that belongs to no Node and so
        # cannot collide with a real machine's row — the alternative, falling back
        # to the sender's fingerprint, would hand a spoofer the takeover back by
        # simply omitting the id.
        instance_id = _clean_instance_id(payload.get("instance_id"))
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

        # Fingerprint. For a checker-origin alert the hub derives it and never
        # reads the sender's claim: identity is the pair (fingerprint, source),
        # so it decides which machine's Alert row — and its history and incident —
        # this push lands on. Auth is a shared API key with no per-node binding,
        # so any holder could otherwise fingerprint a push `check:some-other-node:cpu`
        # and take that machine's row over. The instance_id comes from the envelope
        # the request authenticated with, the same value injected into the labels
        # just above. The node computes this with the same helper
        # (push_to_hub._result_to_alert), so an honest push is unaffected.
        # Non-checker cluster alerts keep the original behaviour.
        checker = labels.get("checker")
        if checker:
            fingerprint = checker_fingerprint(instance_id, checker)
        else:
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
