from django.test import TestCase

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
