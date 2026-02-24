from datetime import datetime
from datetime import timezone as dt_tz
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

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

    # --- _parse_timestamp tests ---

    def test_parse_timestamp_none_returns_now(self):
        """_parse_timestamp(None) should return approximately now."""
        fake_now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=dt_tz.utc)
        with patch(
            "apps.alerts.drivers.newrelic.timezone",
        ) as mock_tz:
            mock_tz.now.return_value = fake_now
            result = self.driver._parse_timestamp(None)
        self.assertEqual(result, fake_now)

    def test_parse_timestamp_valid_unix_seconds(self):
        """Valid Unix timestamp (seconds) should be parsed."""
        result = self.driver._parse_timestamp(1704708000)
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_valid_unix_milliseconds(self):
        """Unix millisecond timestamp (>10000000000) should be divided."""
        result = self.driver._parse_timestamp(1704708000000)
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_valid_iso_string(self):
        """Valid ISO string should be parsed."""
        result = self.driver._parse_timestamp("2024-01-08T10:00:00Z")
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_invalid_returns_now(self):
        """Invalid timestamp should fall back to now."""
        before = timezone.now()
        result = self.driver._parse_timestamp("not-a-timestamp")
        after = timezone.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    # --- fingerprint fallback ---

    def test_classic_fingerprint_fallback(self):
        """Classic alert without incident_id should use generate_fingerprint."""
        payload = {
            "account_id": 123,
            "condition_id": 456,
            "condition_name": "NR: no incident_id",
            "current_state": "open",
            "severity": "WARNING",
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        # generate_fingerprint produces a 16-char hex string
        self.assertTrue(len(alert.fingerprint) > 0)

    def test_workflow_fingerprint_fallback(self):
        """Workflow alert without issueId should use generate_fingerprint."""
        payload = {
            "issueUrl": "https://example.newrelic.test/issues/ISSUE-2",
            "accumulations": {},
            "title": "NR workflow: no issueId",
            "state": "open",
            "priority": "critical",
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertTrue(len(alert.fingerprint) > 0)

    # --- targets array ---

    def test_classic_targets_added_to_labels(self):
        """Targets array should add target_N_name/type to labels."""
        payload = {
            "account_id": 123,
            "condition_id": 456,
            "condition_name": "NR: targets test",
            "current_state": "open",
            "incident_id": 999,
            "severity": "WARNING",
            "targets": [
                {"name": "host-1", "type": "application"},
                {"name": "host-2", "type": "server"},
            ],
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.labels["target_0_name"], "host-1")
        self.assertEqual(alert.labels["target_0_type"], "application")
        self.assertEqual(alert.labels["target_1_name"], "host-2")
        self.assertEqual(alert.labels["target_1_type"], "server")

    def test_classic_targets_limited_to_three(self):
        """Only first 3 targets should be included in labels."""
        targets = [{"name": f"host-{i}", "type": "app"} for i in range(5)]
        payload = {
            "account_id": 123,
            "condition_id": 456,
            "condition_name": "NR: many targets",
            "current_state": "open",
            "incident_id": 888,
            "severity": "WARNING",
            "targets": targets,
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertIn("target_2_name", alert.labels)
        self.assertNotIn("target_3_name", alert.labels)

    def test_classic_targets_empty_list(self):
        """Empty targets list should not add target labels."""
        payload = {
            "account_id": 123,
            "condition_id": 456,
            "condition_name": "NR: empty targets",
            "current_state": "open",
            "incident_id": 777,
            "severity": "WARNING",
            "targets": [],
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertNotIn("target_0_name", alert.labels)

    # --- priority medium/other ---

    def test_workflow_priority_medium_is_warning(self):
        """Workflow priority=medium should map to severity=warning."""
        payload = {
            "issueUrl": "https://example.newrelic.test/issues/ISSUE-3",
            "issueId": "ISSUE-3",
            "title": "NR workflow: medium",
            "state": "open",
            "priority": "medium",
            "accumulations": {},
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "warning")

    def test_workflow_priority_other_is_info(self):
        """Workflow priority not critical/high/medium maps to info."""
        payload = {
            "issueUrl": "https://example.newrelic.test/issues/ISSUE-4",
            "issueId": "ISSUE-4",
            "title": "NR workflow: low",
            "state": "open",
            "priority": "low",
            "accumulations": {},
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "info")

    # --- classic severity mapping ---

    def test_classic_severity_low_is_info(self):
        """Classic severity=low should map to info."""
        payload = {
            "account_id": 123,
            "condition_id": 456,
            "condition_name": "NR: low",
            "current_state": "open",
            "incident_id": 666,
            "severity": "LOW",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "info")

    def test_classic_severity_unknown_defaults_warning(self):
        """Unknown severity string should default to warning."""
        payload = {
            "account_id": 123,
            "condition_id": 456,
            "condition_name": "NR: unknown sev",
            "current_state": "open",
            "incident_id": 555,
            "severity": "BANANA",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "warning")

    # --- validate edge cases ---

    def test_validate_rejects_unrelated_payload(self):
        """Payload without NR keys should be rejected."""
        self.assertFalse(self.driver.validate({"random": "data"}))
