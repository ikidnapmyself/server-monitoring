from datetime import datetime
from unittest.mock import patch

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

    # --- _parse_timestamp tests ---

    def test_parse_timestamp_none_returns_now(self):
        """_parse_timestamp(None) should return approximately now."""
        fake_now = datetime(2024, 6, 15, 12, 0, 0)
        with patch(
            "apps.alerts.drivers.grafana.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = self.driver._parse_timestamp(None)
        self.assertEqual(result, fake_now)

    def test_parse_timestamp_valid_rfc3339(self):
        """Valid RFC3339 timestamp should be parsed correctly."""
        result = self.driver._parse_timestamp("2024-01-08T10:30:00+00:00")
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.hour, 10)

    def test_parse_timestamp_invalid_returns_now(self):
        """Invalid timestamp string should fall back to now."""
        before = datetime.now()
        result = self.driver._parse_timestamp("not-a-timestamp")
        after = datetime.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    # --- fingerprint fallback ---

    def test_fingerprint_fallback_when_missing(self):
        """When no fingerprint, generate_fingerprint should be used."""
        # The sample payload alert has no fingerprint key already
        result = self.driver.parse(self.sample_payload)
        alert = result.alerts[0]
        self.assertTrue(len(alert.fingerprint) > 0)

    # --- _parse_legacy_alert() ---

    def test_parse_legacy_alert_firing(self):
        """Legacy format with evalMatches should parse correctly."""
        payload = {
            "dashboardId": 1,
            "evalMatches": [{"value": 100, "metric": "cpu", "tags": None}],
            "orgId": 1,
            "panelId": 2,
            "ruleId": 10,
            "ruleName": "Legacy CPU Alert",
            "ruleUrl": "http://grafana/d/abc/dashboard",
            "state": "alerting",
            "title": "Legacy CPU Alert",
            "message": "CPU is high",
        }
        self.assertTrue(self.driver.validate(payload))
        result = self.driver.parse(payload)
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "Legacy CPU Alert")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.description, "CPU is high")
        self.assertIn("ruleId", alert.labels)
        self.assertIsNone(alert.ended_at)

    def test_parse_legacy_alert_resolved(self):
        """Legacy format with state=ok should be resolved."""
        payload = {
            "dashboardId": 1,
            "evalMatches": [],
            "orgId": 1,
            "panelId": 2,
            "ruleId": 10,
            "ruleName": "Legacy OK Alert",
            "state": "ok",
            "title": "Legacy OK Alert",
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "warning")
        self.assertIsNotNone(alert.ended_at)

    def test_parse_legacy_alert_warning_state(self):
        """Legacy format with state=pending should default to warning."""
        payload = {
            "dashboardId": 1,
            "evalMatches": [],
            "orgId": 1,
            "panelId": 2,
            "ruleId": 10,
            "ruleName": "Pending Alert",
            "state": "pending",
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "warning")

    # --- description fallback ---

    def test_unified_description_fallback_to_summary(self):
        """When annotations.description is absent, use summary."""
        self.sample_payload["alerts"][0]["annotations"] = {
            "summary": "Summary text",
        }
        result = self.driver.parse(self.sample_payload)
        self.assertEqual(result.alerts[0].description, "Summary text")

    def test_unified_description_fallback_to_message(self):
        """When both description and summary are absent, use message."""
        self.sample_payload["alerts"][0]["annotations"] = {}
        self.sample_payload["alerts"][0]["message"] = "Message text"
        result = self.driver.parse(self.sample_payload)
        self.assertEqual(result.alerts[0].description, "Message text")

    # --- endsAt future filtering ---

    def test_ends_at_far_future_becomes_none(self):
        """endsAt set to far future year should be filtered to None."""
        self.sample_payload["alerts"][0]["endsAt"] = "2099-01-01T00:00:00Z"
        result = self.driver.parse(self.sample_payload)
        self.assertIsNone(result.alerts[0].ended_at)

    def test_ends_at_past_is_preserved(self):
        """endsAt set to a past date should be kept."""
        self.sample_payload["alerts"][0]["endsAt"] = "2024-01-08T11:00:00Z"
        result = self.driver.parse(self.sample_payload)
        self.assertIsNotNone(result.alerts[0].ended_at)

    # --- validate edge cases ---

    def test_validate_with_dashboard_id_and_alerts(self):
        """dashboardId + alerts should validate even without orgId/state."""
        payload = {
            "alerts": [{"status": "firing"}],
            "dashboardId": 5,
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_rejects_unrelated_payload(self):
        """Payload without Grafana keys should be rejected."""
        self.assertFalse(self.driver.validate({"random": "data"}))
