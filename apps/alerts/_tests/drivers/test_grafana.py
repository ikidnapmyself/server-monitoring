from django.test import TestCase

from apps.alerts.drivers import GrafanaDriver


class GrafanaDriverTests(TestCase):
    """Tests for Grafana driver."""

    def setUp(self):
        self.driver = GrafanaDriver()
        self.sample_payload = {
            "receiver": "webhook",
            "status": "firing",
            "orgId": 1,
            "state": "alerting",
            "title": "Test Alert",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "DiskFull",
                        "severity": "warning",
                    },
                    "annotations": {
                        "summary": "Disk is almost full",
                    },
                    "startsAt": "2024-01-08T10:00:00Z",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
            "commonAnnotations": {},
            "externalURL": "http://grafana:3000",
        }

    def test_validate_valid_payload(self):
        self.assertTrue(self.driver.validate(self.sample_payload))

    def test_parse_unified_alerting(self):
        result = self.driver.parse(self.sample_payload)

        self.assertEqual(result.source, "grafana")
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "DiskFull")
        self.assertEqual(alert.severity, "warning")
