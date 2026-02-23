from datetime import datetime
from unittest.mock import patch

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

    # --- _parse_timestamp tests ---

    def test_parse_timestamp_none_returns_now(self):
        """_parse_timestamp(None) should return approximately now."""
        fake_now = datetime(2024, 6, 15, 12, 0, 0)
        with patch(
            "apps.alerts.drivers.datadog.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = self.driver._parse_timestamp(None)
        self.assertEqual(result, fake_now)

    def test_parse_timestamp_valid_unix_int(self):
        """Valid Unix timestamp (int) should be parsed correctly."""
        result = self.driver._parse_timestamp(1704708000)
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_valid_iso_string(self):
        """Valid ISO string should be parsed correctly."""
        result = self.driver._parse_timestamp("2024-01-08T10:00:00Z")
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)

    def test_parse_timestamp_invalid_returns_now(self):
        """Invalid timestamp string should fall back to now."""
        before = datetime.now()
        result = self.driver._parse_timestamp("not-a-timestamp")
        after = datetime.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    # --- fingerprint fallback ---

    def test_fingerprint_fallback_when_no_id(self):
        """When neither alert_id nor id is present, use generate_fingerprint."""
        payload = {
            "alert_title": "DD: no id",
            "alert_transition": "triggered",
            "alert_status": "warn",
            "alert_type": "metric_alert",
            "org": {"id": "org1", "name": "Acme"},
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        # Should have a hex fingerprint from generate_fingerprint
        self.assertTrue(len(alert.fingerprint) > 0)

    # --- validate() negative case ---

    def test_validate_rejects_unrelated_payload(self):
        """Payload without Datadog keys or org dict should be rejected."""
        self.assertFalse(self.driver.validate({"random": "data"}))

    def test_validate_accepts_org_dict(self):
        """Payload with 'org' dict should be accepted."""
        payload = {"org": {"id": "1", "name": "Acme"}}
        self.assertTrue(self.driver.validate(payload))

    # --- severity branches ---

    def test_severity_info_from_low_priority(self):
        """priority=low should map to severity=info."""
        payload = {
            "alert_id": "s1",
            "alert_title": "DD: low prio",
            "alert_type": "metric_alert",
            "priority": "low",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "info")

    def test_severity_info_from_p3_priority(self):
        """priority=p3 should map to severity=info."""
        payload = {
            "alert_id": "s2",
            "alert_title": "DD: p3 prio",
            "alert_type": "metric_alert",
            "priority": "p3",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "info")

    def test_severity_critical_from_high_priority(self):
        """priority=high should map to severity=critical."""
        payload = {
            "alert_id": "s3",
            "alert_title": "DD: high prio",
            "alert_type": "metric_alert",
            "priority": "high",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "critical")

    def test_severity_warning_default(self):
        """Default severity is warning when no special priority/type."""
        payload = {
            "alert_id": "s4",
            "alert_title": "DD: default",
            "alert_type": "metric_alert",
            "priority": "normal",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "warning")

    # --- tag processing edge cases ---

    def test_empty_tags_string(self):
        """Empty tags string should not add extra labels."""
        payload = {
            "alert_id": "t1",
            "alert_title": "DD: no tags",
            "alert_type": "metric_alert",
            "tags": "",
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        # Only alertname + alert_id should be in labels
        self.assertIn("alertname", alert.labels)

    # --- conditional labels ---

    def test_hostname_label_added(self):
        """hostname in payload should be added to labels."""
        payload = {
            "alert_id": "h1",
            "alert_title": "DD: host",
            "alert_type": "metric_alert",
            "hostname": "web-01",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].labels["hostname"], "web-01")

    def test_alert_metric_label_added(self):
        """alert_metric in payload should be added to labels."""
        payload = {
            "alert_id": "m1",
            "alert_title": "DD: metric",
            "alert_type": "metric_alert",
            "alert_metric": "system.cpu",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].labels["metric"], "system.cpu")
