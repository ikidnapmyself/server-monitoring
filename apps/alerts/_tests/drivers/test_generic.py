from datetime import datetime
from unittest.mock import patch

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

    # --- _parse_timestamp tests ---

    def test_parse_timestamp_none_returns_now(self):
        """_parse_timestamp(None) should return approximately now."""
        fake_now = datetime(2024, 6, 15, 12, 0, 0)
        with patch(
            "apps.alerts.drivers.generic.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = self.driver._parse_timestamp(None)
        self.assertEqual(result, fake_now)

    def test_parse_timestamp_valid_unix_seconds(self):
        """Valid Unix timestamp (seconds) should be parsed."""
        result = self.driver._parse_timestamp(1704708000)
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_valid_unix_milliseconds(self):
        """Unix millisecond timestamps (>1e12) should be divided by 1000."""
        result = self.driver._parse_timestamp(1704708000000)
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_valid_iso_string(self):
        """Valid ISO string should be parsed."""
        result = self.driver._parse_timestamp("2024-01-08T10:00:00Z")
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_invalid_string_returns_now(self):
        """Invalid timestamp string should fall back to now."""
        before = datetime.now()
        result = self.driver._parse_timestamp("garbage")
        after = datetime.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    def test_parse_timestamp_datetime_passthrough(self):
        """An already-datetime value should be returned as-is."""
        dt = datetime(2024, 1, 8, 10, 0, 0)
        result = self.driver._parse_timestamp(dt)
        self.assertEqual(result, dt)

    def test_parse_timestamp_invalid_unix_returns_now(self):
        """Extremely large int should fall back to now via OSError."""
        before = datetime.now()
        # This value is way too large for fromtimestamp, triggers OSError
        result = self.driver._parse_timestamp(999999999999999999)
        after = datetime.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    # --- fingerprint fallback ---

    def test_fingerprint_fallback_when_missing(self):
        """When no fingerprint field, generate_fingerprint is used."""
        payload = {
            "name": "No FP Alert",
            "status": "firing",
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertTrue(len(alert.fingerprint) > 0)

    # --- validate() non-list alerts ---

    def test_validate_rejects_alerts_non_list(self):
        """alerts key that isn't a list should fail validation."""
        payload = {"alerts": "not-a-list"}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_empty_payload(self):
        """Payload with no alerts, name, alert_name, or title fails."""
        self.assertFalse(self.driver.validate({"random": "data"}))

    # --- status fallbacks ---

    def test_status_defaults_to_firing(self):
        """When status is missing, it defaults to firing."""
        payload = {"name": "No Status"}
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].status, "firing")

    def test_status_from_state_field(self):
        """When status is None but state is set, use state."""
        payload = {"name": "State Alert", "state": "normal"}
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].status, "resolved")

    def test_unknown_status_with_state_ok(self):
        """Unknown status value but state=ok should resolve to resolved."""
        payload = {
            "name": "Weird Status",
            "status": "unknown",
            "state": "ok",
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].status, "resolved")

    def test_unknown_status_and_state_defaults_firing(self):
        """Unknown status and unknown state defaults to firing."""
        payload = {
            "name": "Weird Status",
            "status": "unknown",
            "state": "unknown",
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].status, "firing")

    # --- severity fallbacks ---

    def test_severity_defaults_to_warning(self):
        """When severity is missing and no priority, default is warning."""
        payload = {"name": "No Sev"}
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].severity, "warning")

    def test_severity_from_priority_when_severity_none(self):
        """When severity is None, priority is used directly."""
        payload = {"name": "Prio Alert", "priority": "critical"}
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].severity, "critical")

    def test_severity_info_from_level(self):
        """level=info should infer severity=info."""
        payload = {
            "name": "Level Alert",
            "severity": "unknown",
            "level": "info",
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].severity, "info")

    def test_severity_critical_from_level_error(self):
        """level=error should infer severity=critical."""
        payload = {
            "name": "Error Level",
            "severity": "banana",
            "level": "error",
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].severity, "critical")

    def test_severity_unknown_no_priority_or_level_defaults_warning(self):
        """Unknown severity with no useful priority/level defaults to warning."""
        payload = {
            "name": "Unknown Sev",
            "severity": "banana",
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].severity, "warning")

    # --- ended_at for resolved ---

    def test_resolved_alert_gets_ended_at(self):
        """Resolved alerts should have ended_at set."""
        payload = {
            "name": "Resolved Alert",
            "status": "resolved",
            "ended_at": "2024-01-08T11:00:00Z",
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertIsNotNone(alert.ended_at)

    def test_resolved_alert_ended_at_now_when_no_field(self):
        """Resolved alert without ended_at field should still get a time."""
        payload = {
            "name": "Resolved No End",
            "status": "resolved",
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        # ended_at is set to now() via _parse_timestamp(None)
        self.assertIsNotNone(alert.ended_at)

    # --- non-dict labels ---

    def test_non_dict_labels_become_empty(self):
        """Labels that aren't a dict should be replaced with empty dict."""
        payload = {
            "name": "Bad Labels",
            "status": "firing",
            "labels": "not-a-dict",
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].labels, {})

    # --- non-dict annotations ---

    def test_non_dict_annotations_become_empty(self):
        """Annotations that aren't a dict should be replaced with empty dict."""
        payload = {
            "name": "Bad Annotations",
            "status": "firing",
            "annotations": ["not", "a", "dict"],
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].annotations, {})
