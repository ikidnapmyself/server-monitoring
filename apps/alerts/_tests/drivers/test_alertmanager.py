from datetime import datetime, timezone as dt_tz
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

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

    # --- _parse_timestamp tests ---

    def test_parse_timestamp_none_returns_now(self):
        """_parse_timestamp(None) should return approximately now."""
        fake_now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=dt_tz.utc)
        with patch(
            "apps.alerts.drivers.alertmanager.timezone",
        ) as mock_tz:
            mock_tz.now.return_value = fake_now
            result = self.driver._parse_timestamp(None)
        self.assertEqual(result, fake_now)

    def test_parse_timestamp_valid_rfc3339(self):
        """Valid RFC3339 timestamp should be parsed correctly."""
        result = self.driver._parse_timestamp("2024-01-08T10:30:00+00:00")
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 8)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.minute, 30)

    def test_parse_timestamp_invalid_returns_now(self):
        """Invalid timestamp string should fall back to now."""
        before = timezone.now()
        result = self.driver._parse_timestamp("not-a-timestamp")
        after = timezone.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    # --- fingerprint fallback tests ---

    def test_fingerprint_fallback_when_missing(self):
        """When no fingerprint field, generate_fingerprint should be used."""
        self.sample_payload["alerts"][0].pop("fingerprint")
        result = self.driver.parse(self.sample_payload)
        alert = result.alerts[0]
        # Should be a hex string from generate_fingerprint
        self.assertTrue(len(alert.fingerprint) > 0)
        self.assertNotEqual(alert.fingerprint, "abc123")

    # --- ended_at future filtering ---

    def test_ended_at_far_future_is_set_to_none(self):
        """endsAt set to a far future year should be filtered to None."""
        self.sample_payload["alerts"][0]["endsAt"] = "2099-01-01T00:00:00Z"
        result = self.driver.parse(self.sample_payload)
        alert = result.alerts[0]
        self.assertIsNone(alert.ended_at)

    # --- description from annotations.summary ---

    def test_description_falls_back_to_summary(self):
        """When annotations.description is absent, use annotations.summary."""
        self.sample_payload["alerts"][0]["annotations"] = {
            "summary": "Summary text only",
        }
        result = self.driver.parse(self.sample_payload)
        alert = result.alerts[0]
        self.assertEqual(alert.description, "Summary text only")
