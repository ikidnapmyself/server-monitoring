"""Tests for the disk temperature checker."""

import sys
from collections import namedtuple
from unittest import mock

from django.test import TestCase

from apps.checkers._tests.checkers.test_describe_contract import RAW
from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.disk_temp import DiskTempChecker

shwtemp = namedtuple("shwtemp", ["label", "current", "high", "critical"])


class DiskTempCheckerTests(TestCase):
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

    def test_healthy_disk_is_ok(self):
        self._patch_sensors({"drivetemp": [shwtemp("sda", 40.0, 60.0, 65.0)]})
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["hottest_c"], 40.0)
        self.assertEqual(result.metrics["hottest_disk"], "sda")
        self.assertEqual(result.metrics["disk_count"], 1)
        self.assertEqual(result.metrics["disks"][0]["critical_c"], 65.0)

    def test_worst_disk_drives_warning(self):
        self._patch_sensors(
            {
                "drivetemp": [
                    shwtemp("sda", 40.0, None, None),
                    shwtemp("sdb", 57.0, None, None),  # warn >= 55
                ]
            }
        )
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.metrics["hottest_disk"], "sdb")
        self.assertIn("sdb", result.message)

    def test_critical_disk(self):
        self._patch_sensors({"nvme": [shwtemp("Composite", 62.0, None, 84.0)]})
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertEqual(result.metrics["hottest_disk"], "Composite")

    def test_non_linux_skips_ok(self):
        self._start(mock.patch.object(sys, "platform", "darwin"))
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.SKIPPED)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["disk_count"], 0)
        self.assertIsNone(result.metrics["hottest_c"])

    def test_no_sensors_skips_ok(self):
        self._patch_sensors({})  # no chips at all
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.SKIPPED)
        self.assertIn("no disk temperature sensors", result.message)

    def test_only_nonpositive_readings_skips_ok(self):
        self._patch_sensors({"drivetemp": [shwtemp("sda", 0.0, None, None)]})
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.SKIPPED)
        self.assertIn("no disk temperature sensors", result.message)

    def test_registered_in_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers import DiskTempChecker as Exported

        self.assertIs(CHECKER_REGISTRY["disk_temp"], Exported)


class DiskTempDescribeTests(TestCase):
    def test_ok_names_the_hottest_disk(self):
        d = DiskTempChecker().describe(CheckStatus.OK, {"hottest_c": 41.0, "hottest_disk": "nvme0"})
        self.assertEqual(d.headline, "Hottest disk nvme0 at 41°C")
        self.assertIsNone(d.action)

    def test_warning_says_what_to_do(self):
        metrics = {"hottest_c": 57.6, "hottest_disk": "sda"}
        d = DiskTempChecker().describe(CheckStatus.WARNING, metrics)
        self.assertEqual(d.headline, "Hottest disk sda at 58°C")
        self.assertIn("airflow", d.action.text)

    def test_no_reading_falls_back_to_the_message(self):
        metrics = {"hottest_c": None, "hottest_disk": None}
        d = DiskTempChecker().describe(CheckStatus.OK, metrics, "No disks")
        self.assertEqual(d.headline, "No disks")

    def test_real_output_reads_in_words(self):
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch(
                "apps.checkers.checkers._sensors.psutil.sensors_temperatures",
                return_value={"drivetemp": [shwtemp("sda", 61.0, 60.0, 65.0)]},
                create=True,
            ),
        ):
            result = DiskTempChecker().check()
        d = DiskTempChecker().describe(result.status, result.metrics, result.message)
        self.assertEqual(d.headline, "Hottest disk sda at 61°C")
        self.assertIsNone(RAW.search(d.headline))
