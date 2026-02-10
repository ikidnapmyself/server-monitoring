from django.test import TestCase

from apps.alerts.drivers.newrelic import NewRelicDriver


class NewRelicDriverTests(TestCase):
    """Tests for New Relic driver."""

    def setUp(self):
        self.driver = NewRelicDriver()

    def test_parse_classic_closed_is_resolved(self):
        payload = {
            "account_id": 123,
            "account_name": "Acme",
            "condition_id": 456,
            "condition_name": "NR: Apdex low",
            "current_state": "closed",
            "details": "SLO violated",
            "incident_id": 789,
            "incident_url": "https://example.newrelic.test/incidents/789",
            "severity": "CRITICAL",
            "timestamp": 1704708000,  # 2024-01-08
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.fingerprint, "789")
        self.assertEqual(alert.name, "NR: Apdex low")
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")

    def test_parse_workflow_high_priority_is_critical(self):
        payload = {
            "issueUrl": "https://example.newrelic.test/issues/ISSUE-1",
            "issueId": "ISSUE-1",
            "title": "NR workflow: error rate",
            "state": "open",
            "priority": "high",
            "createdAt": "2024-01-08T10:00:00Z",
            "accumulations": {"conditionName": ["NR: error rate"]},
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.fingerprint, "ISSUE-1")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
