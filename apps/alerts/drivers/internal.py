"""Internal alert driver — for in-process callers only (NOT webhook-reachable).

Used by ``apps.observability``'s freshness checker to produce Alerts that flow
through the standard alerts → orchestration → notify pipeline. Has no
``signature_header`` by design and is excluded from the
:data:`apps.alerts.drivers.WEBHOOK_DRIVERS` set so it cannot be invoked from
``/alerts/webhook/``.

In-process callers obtain the driver via
``orchestrator.process_webhook(payload, driver="internal")`` which looks the
driver up by name in ``DRIVER_REGISTRY`` rather than going through
auto-detection. Auto-detection (``detect_driver``) is also restricted to
``WEBHOOK_DRIVERS`` so a crafted external payload cannot select this driver.

Payload format (single alert)::

    {
        "source": "observability",
        "fingerprint": "heartbeat-stale:foo",
        "title": "Heartbeat stale: foo",
        "severity": "warning",
        "labels": {"job": "foo", "max_age_seconds": 900},
        "description": "Hourly cron",   # optional
    }
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload


class InternalDriver(BaseAlertDriver):
    """In-process alert driver (not exposed to /alerts/webhook/)."""

    name = "internal"
    signature_header = None  # explicit: not webhook-reachable

    REQUIRED: tuple[str, ...] = ("source", "fingerprint", "title", "severity", "labels")

    def validate(self, payload: dict[str, Any]) -> bool:
        """Return True iff all required fields are present in ``payload``."""
        return all(k in payload for k in self.REQUIRED)

    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Translate an internal-format payload into a ``ParsedPayload``.

        The result is a ``ParsedPayload`` containing exactly one
        ``ParsedAlert`` so it can flow through the same code path as
        webhook-sourced alerts.
        """
        labels_raw = payload.get("labels") or {}
        labels = {str(k): str(v) for k, v in labels_raw.items()}

        alert = ParsedAlert(
            fingerprint=str(payload["fingerprint"]),
            name=str(payload["title"]),
            status="firing",
            severity=str(payload["severity"]),
            description=str(payload.get("description", "")),
            labels=labels,
            started_at=timezone.now(),
            raw_payload=dict(payload),
        )

        return ParsedPayload(
            alerts=[alert],
            source=str(payload["source"]),
            raw_payload=dict(payload),
        )
