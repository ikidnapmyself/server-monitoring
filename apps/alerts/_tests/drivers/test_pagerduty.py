from datetime import datetime
from datetime import timezone as dt_tz
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

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

    # --- _parse_timestamp tests ---

    def test_parse_timestamp_none_returns_now(self):
        """_parse_timestamp(None) should return approximately now."""
        fake_now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=dt_tz.utc)
        with patch(
            "apps.alerts.drivers.pagerduty.timezone",
        ) as mock_tz:
            mock_tz.now.return_value = fake_now
            result = self.driver._parse_timestamp(None)
        self.assertEqual(result, fake_now)

    def test_parse_timestamp_valid_iso(self):
        """Valid ISO 8601 timestamp should be parsed."""
        result = self.driver._parse_timestamp("2024-01-08T10:00:00Z")
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)

    def test_parse_timestamp_invalid_returns_now(self):
        """Invalid timestamp should fall back to now."""
        before = timezone.now()
        result = self.driver._parse_timestamp("not-a-timestamp")
        after = timezone.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    # --- fingerprint fallback ---

    def test_v3_fingerprint_fallback_when_no_id(self):
        """V3 event without data.id should use generate_fingerprint."""
        payload = {
            "event": {
                "id": "evt_3",
                "event_type": "incident.triggered",
                "resource_type": "incident",
                "occurred_at": "2024-01-08T10:00:00Z",
                "data": {
                    "title": "PD: no id",
                    "urgency": "high",
                    "service": {"id": "svc_1", "summary": "API"},
                },
            }
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertTrue(len(alert.fingerprint) > 0)

    # --- _parse_v2_message() ---

    def test_v2_validate_valid(self):
        """V2 payload with messages containing incident should validate."""
        payload = {
            "messages": [
                {
                    "type": "incident.trigger",
                    "incident": {
                        "id": "inc_v2_1",
                        "incident_number": 42,
                        "title": "PD V2: disk full",
                        "urgency": "high",
                        "trigger_summary_data": {
                            "subject": "PD V2: triggered subject",
                        },
                        "service": {"name": "DB Service"},
                        "created_on": "2024-01-08T10:00:00Z",
                        "description": "Disk full on db-01",
                    },
                }
            ]
        }
        self.assertTrue(self.driver.validate(payload))

    def test_v2_parse_triggered(self):
        """V2 message with incident.trigger should be firing."""
        payload = {
            "messages": [
                {
                    "type": "incident.trigger",
                    "incident": {
                        "id": "inc_v2_1",
                        "incident_number": 42,
                        "title": "PD V2: disk full",
                        "urgency": "high",
                        "trigger_summary_data": {
                            "subject": "PD V2: triggered subject",
                        },
                        "service": {"name": "DB Service"},
                        "created_on": "2024-01-08T10:00:00Z",
                        "description": "Disk full on db-01",
                    },
                }
            ]
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(len(parsed.alerts), 1)

        alert = parsed.alerts[0]
        self.assertEqual(alert.fingerprint, "inc_v2_1")
        self.assertEqual(alert.name, "PD V2: triggered subject")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.labels["service_name"], "DB Service")

    def test_v2_parse_resolved(self):
        """V2 message with incident.resolve should be resolved."""
        payload = {
            "messages": [
                {
                    "type": "incident.resolve",
                    "incident": {
                        "id": "inc_v2_2",
                        "incident_number": 43,
                        "title": "PD V2: resolved",
                        "urgency": "low",
                        "service": {"name": "API Service"},
                        "created_on": "2024-01-08T11:00:00Z",
                    },
                }
            ]
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "warning")

    def test_v2_fingerprint_fallback(self):
        """V2 message without incident.id uses generate_fingerprint."""
        payload = {
            "messages": [
                {
                    "type": "incident.trigger",
                    "incident": {
                        "title": "PD V2: no id",
                        "urgency": "high",
                        "service": {},
                    },
                }
            ]
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertTrue(len(alert.fingerprint) > 0)

    def test_v2_multiple_messages(self):
        """V2 payload with multiple messages should produce multiple alerts."""
        payload = {
            "messages": [
                {
                    "type": "incident.trigger",
                    "incident": {
                        "id": "inc_v2_a",
                        "title": "Alert A",
                        "urgency": "high",
                        "service": {},
                    },
                },
                {
                    "type": "incident.resolve",
                    "incident": {
                        "id": "inc_v2_b",
                        "title": "Alert B",
                        "urgency": "low",
                        "service": {},
                    },
                },
            ]
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(len(parsed.alerts), 2)
        self.assertEqual(parsed.alerts[0].status, "firing")
        self.assertEqual(parsed.alerts[1].status, "resolved")

    # --- V2 validate ---

    def test_v2_validate_with_type_key(self):
        """V2 payload with messages[0].type should validate."""
        payload = {
            "messages": [
                {
                    "type": "incident.trigger",
                }
            ]
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_rejects_unrelated_payload(self):
        """Payload without PagerDuty keys should be rejected."""
        self.assertFalse(self.driver.validate({"random": "data"}))

    def test_validate_rejects_empty_messages(self):
        """Empty messages list should be rejected."""
        self.assertFalse(self.driver.validate({"messages": []}))

    # --- priority severity mapping ---

    def test_v3_priority_p3_low_maps_to_info(self):
        """V3 event with priority containing 'P3' should be info."""
        payload = {
            "event": {
                "id": "evt_p3",
                "event_type": "incident.triggered",
                "resource_type": "incident",
                "data": {
                    "id": "inc_p3",
                    "title": "PD: P3",
                    "urgency": "high",
                    "priority": {"name": "P3 - Low"},
                    "service": {},
                },
            }
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "info")

    def test_v3_priority_critical_maps_to_critical(self):
        """V3 event with priority containing 'critical' is critical."""
        payload = {
            "event": {
                "id": "evt_crit",
                "event_type": "incident.triggered",
                "resource_type": "incident",
                "data": {
                    "id": "inc_crit",
                    "title": "PD: Critical",
                    "urgency": "low",
                    "priority": {"name": "Critical"},
                    "service": {},
                },
            }
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "critical")

    def test_v3_no_priority_uses_urgency(self):
        """V3 event without priority should use urgency for severity."""
        payload = {
            "event": {
                "id": "evt_np",
                "event_type": "incident.triggered",
                "resource_type": "incident",
                "data": {
                    "id": "inc_np",
                    "title": "PD: No Priority",
                    "urgency": "low",
                    "service": {},
                },
            }
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].severity, "warning")
