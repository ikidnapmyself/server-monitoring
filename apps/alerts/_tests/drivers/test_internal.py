"""Tests for the internal alert driver.

The internal driver is for in-process callers only (e.g. the observability
freshness checker). It is registered in DRIVER_REGISTRY so it can be looked
up by name via ``get_driver("internal")`` / ``process_webhook(..., driver="internal")``,
but it is intentionally excluded from ``WEBHOOK_DRIVERS`` so a crafted
external payload cannot route to it via ``/alerts/webhook/``.
"""

from __future__ import annotations

from apps.alerts.drivers.internal import InternalDriver


def _full_payload() -> dict:
    return {
        "source": "observability",
        "fingerprint": "heartbeat-stale:foo",
        "title": "Heartbeat stale: foo",
        "severity": "warning",
        "labels": {"job": "foo", "max_age_seconds": 900},
        "description": "Hourly cron",
    }


def test_signature_header_is_none():
    """Not webhook-reachable by design."""
    assert InternalDriver.signature_header is None


def test_validate_accepts_full_payload():
    d = InternalDriver()
    assert (
        d.validate(
            {
                "source": "observability",
                "fingerprint": "heartbeat-stale:foo",
                "title": "stale",
                "severity": "warning",
                "labels": {"job": "foo"},
            }
        )
        is True
    )


def test_validate_rejects_missing_field():
    d = InternalDriver()
    assert d.validate({"source": "x", "title": "t"}) is False


def test_parse_round_trips():
    """Parsing a full payload yields a ParsedPayload whose single alert
    carries the fingerprint, title (as name), severity, labels, and description
    that the caller supplied."""
    d = InternalDriver()
    parsed = d.parse(_full_payload())

    assert parsed.source == "observability"
    assert len(parsed.alerts) == 1

    alert = parsed.alerts[0]
    assert alert.fingerprint == "heartbeat-stale:foo"
    assert alert.name == "Heartbeat stale: foo"
    assert alert.severity == "warning"
    assert alert.labels == {"job": "foo", "max_age_seconds": "900"}
    assert alert.description == "Hourly cron"
    assert alert.status == "firing"


def test_parse_defaults_description_when_missing():
    """``description`` is optional; missing should produce an empty string."""
    d = InternalDriver()
    payload = {
        "source": "observability",
        "fingerprint": "heartbeat-stale:bar",
        "title": "stale",
        "severity": "warning",
        "labels": {"job": "bar"},
    }
    parsed = d.parse(payload)
    assert parsed.alerts[0].description == ""


def test_internal_driver_not_under_webhook_dispatch():
    """The internal driver MUST NOT be reachable from /alerts/webhook/."""
    from apps.alerts.drivers import WEBHOOK_DRIVERS

    assert "internal" not in WEBHOOK_DRIVERS


def test_internal_driver_registered_for_in_process_lookup():
    """It is still in DRIVER_REGISTRY so ``get_driver("internal")`` works."""
    from apps.alerts.drivers import DRIVER_REGISTRY, get_driver

    assert "internal" in DRIVER_REGISTRY
    assert isinstance(get_driver("internal"), InternalDriver)


def test_detect_driver_never_returns_internal():
    """Auto-detection from /alerts/webhook/ must never select the internal driver,
    even when the payload happens to satisfy ``InternalDriver.validate``."""
    from apps.alerts.drivers import detect_driver

    payload = {
        "source": "observability",
        "fingerprint": "heartbeat-stale:foo",
        "title": "stale",
        "severity": "warning",
        "labels": {"job": "foo"},
    }
    driver = detect_driver(payload)
    assert not isinstance(driver, InternalDriver)
