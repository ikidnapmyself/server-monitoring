"""Tests for the disk temperature checker."""

import sys
from collections import namedtuple
from unittest import mock

from django.test import TestCase

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
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["disk_count"], 0)
        self.assertIsNone(result.metrics["hottest_c"])

    def test_no_sensors_skips_ok(self):
        self._patch_sensors({})  # no chips at all
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("no disk temperature sensors", result.message)

    def test_only_nonpositive_readings_skips_ok(self):
        self._patch_sensors({"drivetemp": [shwtemp("sda", 0.0, None, None)]})
        result = DiskTempChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("no disk temperature sensors", result.message)

    def test_registered_in_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers import DiskTempChecker as Exported

        self.assertIs(CHECKER_REGISTRY["disk_temp"], Exported)
