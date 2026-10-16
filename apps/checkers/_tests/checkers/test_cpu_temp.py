"""Tests for the CPU temperature checker."""

import sys
from collections import namedtuple
from unittest import mock

from django.test import TestCase

from apps.checkers._tests.checkers.test_describe_contract import RAW
from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.cpu_temp import CPUTempChecker

shwtemp = namedtuple("shwtemp", ["label", "current", "high", "critical"])


class CPUTempCheckerTests(TestCase):
    def _patch_sensors(self, raw):
        """Force Linux and stub psutil.sensors_temperatures() to return ``raw``."""
        self._start(mock.patch.object(sys, "platform", "linux"))
        self._start(
            mock.patch(
                "apps.checkers.checkers._sensors.psutil.sensors_temperatures",
                return_value=raw,
                create=True,
            )
        )

    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_healthy_cpu_is_ok(self):
        self._patch_sensors({"coretemp": [shwtemp("Package id 0", 55.0, 100.0, 100.0)]})
        result = CPUTempChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["hottest_c"], 55.0)
        self.assertEqual(result.metrics["hottest_sensor"], "Package id 0")
        self.assertEqual(result.metrics["sensor_count"], 1)

    def test_hottest_core_drives_warning(self):
        self._patch_sensors(
            {
                "coretemp": [
                    shwtemp("Core 0", 60.0, None, None),
                    shwtemp("Core 1", 82.0, None, None),  # warn >= 80
                ]
            }
        )
        result = CPUTempChecker().check()
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.metrics["hottest_sensor"], "Core 1")
        self.assertIn("Core 1", result.message)

    def test_critical_cpu(self):
        self._patch_sensors({"k10temp": [shwtemp("Tctl", 92.0, None, None)]})
        result = CPUTempChecker().check()
        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertEqual(result.metrics["hottest_sensor"], "Tctl")

    def test_ignores_non_cpu_chips(self):
        # A hot disk sensor must not affect the CPU checker.
        self._patch_sensors(
            {
                "drivetemp": [shwtemp("sda", 95.0, None, None)],
                "coretemp": [shwtemp("Package id 0", 50.0, None, None)],
            }
        )
        result = CPUTempChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["hottest_c"], 50.0)

    def test_non_linux_skips_ok(self):
        self._start(mock.patch.object(sys, "platform", "darwin"))
        result = CPUTempChecker().check()
        self.assertEqual(result.status, CheckStatus.SKIPPED)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["sensor_count"], 0)
        self.assertIsNone(result.metrics["hottest_c"])

    def test_no_sensors_skips_ok(self):
        self._patch_sensors({})
        result = CPUTempChecker().check()
        self.assertEqual(result.status, CheckStatus.SKIPPED)
        self.assertIn("no CPU temperature sensors", result.message)

    def test_registered_in_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers import CPUTempChecker as Exported

        self.assertIs(CHECKER_REGISTRY["cpu_temp"], Exported)


class CPUTempDescribeTests(TestCase):
    def test_ok_names_the_hottest_sensor(self):
        metrics = {"hottest_c": 52.4, "hottest_sensor": "Package id 0"}
        d = CPUTempChecker().describe(CheckStatus.OK, metrics)
        self.assertEqual(d.headline, "Hottest sensor Package id 0 at 52°C")
        self.assertIsNone(d.action)

    def test_critical_says_what_to_do(self):
        metrics = {"hottest_c": 93.0, "hottest_sensor": "Core 1"}
        d = CPUTempChecker().describe(CheckStatus.CRITICAL, metrics)
        self.assertEqual(d.headline, "Hottest sensor Core 1 at 93°C")
        self.assertIn("fans", d.action.text)

    def test_no_reading_falls_back_to_the_message(self):
        metrics = {"hottest_c": None, "hottest_sensor": None}
        d = CPUTempChecker().describe(CheckStatus.OK, metrics, "No sensors")
        self.assertEqual(d.headline, "No sensors")

    def test_real_output_reads_in_words(self):
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch(
                "apps.checkers.checkers._sensors.psutil.sensors_temperatures",
                return_value={"coretemp": [shwtemp("Core 1", 82.0, None, None)]},
                create=True,
            ),
        ):
            result = CPUTempChecker().check()
        d = CPUTempChecker().describe(result.status, result.metrics, result.message)
        self.assertEqual(d.headline, "Hottest sensor Core 1 at 82°C")
        self.assertIsNone(RAW.search(d.headline))
