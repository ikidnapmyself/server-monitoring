from django.test import TestCase

from apps.alerts.drivers import AlertManagerDriver


class AlertManagerDriverTests(TestCase):
    """Tests for AlertManager driver."""

    def setUp(self):
        self.driver = AlertManagerDriver()
        self.sample_payload = {
            "version": "4",
            "groupKey": '{}:{alertname="TestAlert"}',
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                        "severity": "critical",
                        "instance": "server1:9090",
                    },
                    "annotations": {
                        "summary": "High CPU usage detected",
                        "description": "CPU usage is above 90%",
                    },
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "abc123",
                }
            ],
            "groupLabels": {"alertname": "HighCPU"},
            "commonLabels": {"alertname": "HighCPU"},
            "commonAnnotations": {},
            "externalURL": "http://alertmanager:9093",
        }

    def test_validate_valid_payload(self):
        self.assertTrue(self.driver.validate(self.sample_payload))

    def test_validate_invalid_payload(self):
        self.assertFalse(self.driver.validate({"random": "data"}))

    def test_parse_payload(self):
        result = self.driver.parse(self.sample_payload)

        self.assertEqual(result.source, "alertmanager")
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "HighCPU")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.fingerprint, "abc123")

    def test_parse_resolved_alert(self):
        self.sample_payload["alerts"][0]["status"] = "resolved"
        self.sample_payload["alerts"][0]["endsAt"] = "2024-01-08T11:00:00Z"

        result = self.driver.parse(self.sample_payload)
        alert = result.alerts[0]

        self.assertEqual(alert.status, "resolved")
        self.assertIsNotNone(alert.ended_at)
