from django.test import TestCase
from django.utils import timezone

from apps.alerts.drivers import (
    AlertManagerDriver,
    GenericWebhookDriver,
    GrafanaDriver,
    ParsedAlert,
    detect_driver,
    get_driver,
)


class DriverDetectionTests(TestCase):
    """Tests for auto-detection of drivers."""

    def test_detect_alertmanager(self):
        payload = {
            "alerts": [],
            "status": "firing",
            "groupKey": "test",
            "receiver": "webhook",
            "groupLabels": {},
            "commonLabels": {},
        }

        driver = detect_driver(payload)
        self.assertIsInstance(driver, AlertManagerDriver)

    def test_detect_grafana(self):
        payload = {
            "alerts": [],
            "orgId": 1,
            "state": "alerting",
        }

        driver = detect_driver(payload)
        self.assertIsInstance(driver, GrafanaDriver)

    def test_fallback_to_generic(self):
        payload = {"name": "Custom Alert"}

        driver = detect_driver(payload)
        self.assertIsInstance(driver, GenericWebhookDriver)

    def test_get_driver_by_name(self):
        driver = get_driver("alertmanager")
        self.assertIsInstance(driver, AlertManagerDriver)

    def test_get_driver_invalid_name(self):
        with self.assertRaises(ValueError):
            get_driver("nonexistent")

    def test_detect_driver_returns_none_for_empty_payload(self):
        """Empty dict should not match any driver, including generic."""
        driver = detect_driver({})
        self.assertIsNone(driver)


class ParsedAlertNormalizationTests(TestCase):
    """Tests for ParsedAlert __post_init__ normalization."""

    def test_invalid_status_normalizes_to_firing(self):
        """A status not in ('firing', 'resolved') should become 'firing'."""
        alert = ParsedAlert(
            fingerprint="fp1",
            name="Test",
            status="bogus",
            started_at=timezone.now(),
        )
        self.assertEqual(alert.status, "firing")

    def test_invalid_severity_normalizes_to_warning(self):
        """A severity not in ('critical', 'warning', 'info') should become 'warning'."""
        alert = ParsedAlert(
            fingerprint="fp2",
            name="Test",
            status="firing",
            severity="bogus",
            started_at=timezone.now(),
        )
        self.assertEqual(alert.severity, "warning")
