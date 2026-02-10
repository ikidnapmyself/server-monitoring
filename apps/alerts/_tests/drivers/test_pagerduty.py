from django.test import TestCase

from apps.alerts.drivers.pagerduty import PagerDutyDriver


class PagerDutyDriverTests(TestCase):
    """Tests for PagerDuty driver."""

    def setUp(self):
        self.driver = PagerDutyDriver()

    def test_validate_and_parse_v3_event(self):
        payload = {
            "event": {
                "id": "evt_1",
                "event_type": "incident.triggered",
                "resource_type": "incident",
                "occurred_at": "2024-01-08T10:00:00Z",
                "data": {
                    "id": "inc_1",
                    "type": "incident",
                    "status": "triggered",
                    "title": "PD: CPU high",
                    "urgency": "high",
                    "priority": {"name": "P1"},
                    "service": {"id": "svc_1", "summary": "API"},
                    "html_url": "https://example.pagerduty.test/incidents/inc_1",
                },
            }
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.source, "pagerduty")
        self.assertEqual(len(parsed.alerts), 1)

        alert = parsed.alerts[0]
        self.assertEqual(alert.fingerprint, "inc_1")
        self.assertEqual(alert.name, "PD: CPU high")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.labels["service_name"], "API")

    def test_parse_v3_resolved_from_event_type(self):
        payload = {
            "event": {
                "id": "evt_2",
                "event_type": "incident.resolved",
                "resource_type": "incident",
                "occurred_at": "2024-01-08T11:00:00Z",
                "data": {
                    "id": "inc_2",
                    "title": "PD: Disk full",
                    "urgency": "low",
                    "service": {"id": "svc_2", "summary": "DB"},
                },
            }
        }

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "warning")
