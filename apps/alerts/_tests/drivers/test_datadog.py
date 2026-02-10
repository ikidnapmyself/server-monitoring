from django.test import TestCase

from apps.alerts.drivers.datadog import DatadogDriver


class DatadogDriverTests(TestCase):
    """Tests for Datadog driver."""

    def setUp(self):
        self.driver = DatadogDriver()

    def test_tags_parse_and_resolved_state(self):
        payload = {
            "alert_id": "123",
            "alert_title": "DD: latency",
            "alert_transition": "recovered",
            "alert_status": "ok",
            "alert_type": "error",
            "last_updated": "2024-01-08T10:00:00Z",
            "tags": "service:api,env:prod,flag",
            "url": "https://example.datadog.test/alerts/123",
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.labels["service"], "api")
        self.assertEqual(alert.labels["env"], "prod")
        self.assertEqual(alert.labels["flag"], "true")

    def test_tags_parse_list_form(self):
        payload = {
            "alert_id": "456",
            "alert_title": "DD: memory",
            "alert_transition": "triggered",
            "alert_status": "warn",
            "alert_type": "metric_alert",
            "last_updated": "2024-01-08T10:00:00Z",
            "tags": ["service:worker", "env:staging", "canary"],
        }

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.labels["service"], "worker")
        self.assertEqual(alert.labels["env"], "staging")
        self.assertEqual(alert.labels["canary"], "true")
