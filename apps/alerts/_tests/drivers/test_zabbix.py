from datetime import datetime, timezone as dt_tz
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.alerts.drivers.zabbix import ZabbixDriver


class ZabbixDriverTests(TestCase):
    """Tests for Zabbix driver."""

    def setUp(self):
        self.driver = ZabbixDriver()

    def test_numeric_severity_and_event_value_resolved(self):
        payload = {
            "event_id": "1001",
            "trigger_id": "2002",
            "trigger_name": "ZBX: Disk full",
            "trigger_severity": "5",
            "event_value": "0",  # OK
            "host_name": "server-1",
            "event_date": "2024.01.08",
            "event_time": "10:00:00",
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.fingerprint, "1001")
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.labels["host_name"], "server-1")

    def test_problem_status_is_firing(self):
        payload = {
            "event_id": "1002",
            "trigger_id": "2003",
            "trigger_name": "ZBX: CPU high",
            "trigger_severity": "2",
            "trigger_status": "PROBLEM",
            "host_name": "server-2",
        }

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "warning")

    # --- _parse_timestamp tests ---

    def test_parse_timestamp_none_returns_now(self):
        """_parse_timestamp(None) should return approximately now."""
        fake_now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=dt_tz.utc)
        with patch(
            "apps.alerts.drivers.zabbix.timezone",
        ) as mock_tz:
            mock_tz.now.return_value = fake_now
            result = self.driver._parse_timestamp(None)
        self.assertEqual(result, fake_now)

    def test_parse_timestamp_valid_unix_int(self):
        """Valid Unix timestamp (int) should be parsed."""
        result = self.driver._parse_timestamp(1704708000)
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_valid_zabbix_format(self):
        """Zabbix date format YYYY.MM.DD HH:MM:SS should be parsed."""
        result = self.driver._parse_timestamp("2024.01.08 10:00:00")
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 8)

    def test_parse_timestamp_iso_format(self):
        """ISO format should be parsed as fallback."""
        result = self.driver._parse_timestamp("2024-01-08T10:00:00Z")
        self.assertEqual(result.year, 2024)

    def test_parse_timestamp_invalid_returns_now(self):
        """Invalid timestamp should fall back to now."""
        before = timezone.now()
        result = self.driver._parse_timestamp("not-a-timestamp")
        after = timezone.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    def test_parse_timestamp_dd_mm_yyyy_format(self):
        """DD.MM.YYYY HH:MM:SS format should be parsed."""
        result = self.driver._parse_timestamp("08.01.2024 10:00:00")
        self.assertEqual(result.year, 2024)

    # --- fingerprint fallback ---

    def test_fingerprint_fallback_when_no_ids(self):
        """When no event_id or trigger_id, use generate_fingerprint."""
        payload = {
            "event_source": "0",
            "event_value": "1",
            "trigger_name": "ZBX: no id alert",
            "trigger_severity": "3",
            "host_name": "server-3",
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertTrue(len(alert.fingerprint) > 0)

    def test_fingerprint_from_trigger_id_when_no_event_id(self):
        """When event_id is absent but trigger_id exists, use trigger_id."""
        payload = {
            "trigger_id": "5001",
            "trigger_name": "ZBX: trigger only",
            "trigger_severity": "3",
            "host_name": "server-4",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].fingerprint, "5001")

    # --- validate() event_source ---

    def test_validate_event_source_format(self):
        """event_source + event_value should validate."""
        payload = {
            "event_source": "0",
            "event_value": "1",
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_rejects_unrelated_payload(self):
        """Payload without Zabbix keys should be rejected."""
        self.assertFalse(self.driver.validate({"random": "data"}))

    # --- trigger_status="OK" ---

    def test_trigger_status_ok_is_resolved(self):
        """trigger_status=OK should set status to resolved."""
        payload = {
            "event_id": "1003",
            "trigger_id": "2004",
            "trigger_name": "ZBX: OK status",
            "trigger_severity": "1",
            "trigger_status": "OK",
            "host_name": "server-5",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].status, "resolved")

    def test_event_status_resolved_is_resolved(self):
        """event_status=RESOLVED should set status to resolved."""
        payload = {
            "event_id": "1004",
            "trigger_id": "2005",
            "trigger_name": "ZBX: event resolved",
            "trigger_severity": "3",
            "event_status": "RESOLVED",
            "host_name": "server-6",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].status, "resolved")

    def test_event_status_ok_is_resolved(self):
        """event_status=OK should set status to resolved."""
        payload = {
            "event_id": "1005",
            "trigger_id": "2006",
            "trigger_name": "ZBX: event ok",
            "trigger_severity": "3",
            "event_status": "OK",
            "host_name": "server-7",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].status, "resolved")

    # --- conditional labels ---

    def test_item_name_added_to_labels(self):
        """item_name in payload should be added to labels."""
        payload = {
            "event_id": "1006",
            "trigger_id": "2007",
            "trigger_name": "ZBX: item name",
            "trigger_severity": "3",
            "host_name": "server-8",
            "item_name": "cpu.idle",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].labels["item_name"], "cpu.idle")

    def test_host_group_added_to_labels(self):
        """host_group in payload should be added to labels."""
        payload = {
            "event_id": "1007",
            "trigger_id": "2008",
            "trigger_name": "ZBX: host group",
            "trigger_severity": "3",
            "host_name": "server-9",
            "host_group": "Linux servers",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].labels["host_group"], "Linux servers")

    def test_no_item_name_or_host_group_in_labels(self):
        """Without item_name/host_group, those keys should be absent."""
        payload = {
            "event_id": "1008",
            "trigger_id": "2009",
            "trigger_name": "ZBX: minimal",
            "trigger_severity": "3",
            "host_name": "server-10",
        }
        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertNotIn("item_name", alert.labels)
        self.assertNotIn("host_group", alert.labels)

    # --- description fallback ---

    def test_description_from_alert_message(self):
        """alert_message should be used for description."""
        payload = {
            "event_id": "1009",
            "trigger_id": "2010",
            "trigger_name": "ZBX: alert msg",
            "trigger_severity": "3",
            "host_name": "server-11",
            "alert_message": "Disk space low",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].description, "Disk space low")

    def test_description_from_event_message(self):
        """event_message should be used when alert_message is absent."""
        payload = {
            "event_id": "1010",
            "trigger_id": "2011",
            "trigger_name": "ZBX: event msg",
            "trigger_severity": "3",
            "host_name": "server-12",
            "event_message": "Memory pressure",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].description, "Memory pressure")

    def test_description_fallback_from_item_name_and_value(self):
        """When no messages, item_name + item_value build description."""
        payload = {
            "event_id": "1011",
            "trigger_id": "2012",
            "trigger_name": "ZBX: item desc",
            "trigger_severity": "3",
            "host_name": "server-13",
            "item_name": "cpu.load",
            "item_value": "95.5",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].description, "cpu.load: 95.5")

    def test_description_empty_when_no_source(self):
        """When no message or item_name+item_value, description is empty."""
        payload = {
            "event_id": "1012",
            "trigger_id": "2013",
            "trigger_name": "ZBX: no desc",
            "trigger_severity": "3",
            "host_name": "server-14",
        }
        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.alerts[0].description, "")
