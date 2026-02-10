from django.test import TestCase

from apps.alerts.drivers import (
    AlertManagerDriver,
    GenericWebhookDriver,
    GrafanaDriver,
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
