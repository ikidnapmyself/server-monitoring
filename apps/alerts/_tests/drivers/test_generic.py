from django.test import TestCase

from apps.alerts.drivers import GenericWebhookDriver


class GenericDriverTests(TestCase):
    """Tests for generic webhook driver."""

    def setUp(self):
        self.driver = GenericWebhookDriver()

    def test_parse_single_alert(self):
        payload = {
            "name": "Custom Alert",
            "status": "firing",
            "severity": "warning",
            "description": "Something happened",
        }

        result = self.driver.parse(payload)
        self.assertEqual(len(result.alerts), 1)
        self.assertEqual(result.alerts[0].name, "Custom Alert")

    def test_parse_multiple_alerts(self):
        payload = {
            "alerts": [
                {"name": "Alert 1", "status": "firing"},
                {"name": "Alert 2", "status": "resolved"},
            ]
        }

        result = self.driver.parse(payload)
        self.assertEqual(len(result.alerts), 2)

    def test_flexible_field_names(self):
        """Test that driver accepts various field name conventions."""
        payload = {
            "title": "My Alert",  # instead of "name"
            "state": "ok",  # instead of "status"
            "priority": "high",  # instead of "severity"
            "message": "Alert description",  # instead of "description"
        }

        result = self.driver.parse(payload)
        alert = result.alerts[0]

        self.assertEqual(alert.name, "My Alert")
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")
