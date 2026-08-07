"""Tests for the IO strain checker."""

import sys
from collections import namedtuple
from unittest import mock

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.io_strain import IOStrainChecker, compute_io_stats

# Minimal subset of psutil's sdiskio with the fields the checker uses.
sdiskio = namedtuple(
    "sdiskio", ["read_count", "write_count", "read_bytes", "write_bytes", "busy_time"]
)


def _c(read_count=0, write_count=0, read_bytes=0, write_bytes=0, busy_time=0):
    return sdiskio(read_count, write_count, read_bytes, write_bytes, busy_time)


class ComputeIoStatsTests(TestCase):
    def test_utilization_from_busy_time(self):
        first = {"sda": _c(busy_time=0)}
        second = {"sda": _c(busy_time=500)}  # 500ms busy over 1s = 50%
        stats = compute_io_stats(first, second, 1.0)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].disk, "sda")
        self.assertEqual(stats[0].util_percent, 50.0)

    def test_utilization_clamped_to_100(self):
        stats = compute_io_stats({"sda": _c(busy_time=0)}, {"sda": _c(busy_time=1500)}, 1.0)
        self.assertEqual(stats[0].util_percent, 100.0)

    def test_throughput_and_iops(self):
        first = {"sda": _c(read_count=0, write_count=0, read_bytes=0, write_bytes=0, busy_time=0)}
        second = {
            "sda": _c(
                read_count=100,
                write_count=50,
                read_bytes=2_000_000,
                write_bytes=1_000_000,
                busy_time=100,
            )
        }
        d = compute_io_stats(first, second, 2.0)[0]
        self.assertEqual(d.read_mb_s, 1.0)  # 2 MB over 2 s
        self.assertEqual(d.write_mb_s, 0.5)
        self.assertEqual(d.read_iops, 50.0)  # 100 ops over 2 s
        self.assertEqual(d.write_iops, 25.0)

    def test_disk_absent_from_first_is_skipped(self):
        stats = compute_io_stats({}, {"sda": _c(busy_time=100)}, 1.0)
        self.assertEqual(stats, [])

    def test_missing_busy_time_is_skipped(self):
        first = {"sda": _c(busy_time=None)}
        second = {"sda": _c(busy_time=None)}
        self.assertEqual(compute_io_stats(first, second, 1.0), [])

    def test_nonpositive_elapsed_returns_empty(self):
        self.assertEqual(compute_io_stats({"sda": _c()}, {"sda": _c()}, 0.0), [])


class IOStrainCheckerTests(TestCase):
    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch(self, first, second):
        """Force Linux, stub two disk_io_counters reads, and pin a 1s interval."""
        self._start(mock.patch.object(sys, "platform", "linux"))
        self._start(mock.patch("apps.checkers.checkers.io_strain.time.sleep"))
        self._start(
            mock.patch("apps.checkers.checkers.io_strain.time.perf_counter", side_effect=[0.0, 1.0])
        )
        self._start(
            mock.patch(
                "apps.checkers.checkers.io_strain.psutil.disk_io_counters",
                side_effect=[first, second],
            )
        )

    def test_low_util_is_ok(self):
        self._patch({"sda": _c(busy_time=0)}, {"sda": _c(busy_time=100)})  # 10%
        result = IOStrainChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["busiest_disk"], "sda")
        self.assertEqual(result.metrics["busiest_util_percent"], 10.0)

    def test_busiest_disk_drives_warning(self):
        self._patch(
            {"sda": _c(busy_time=0), "sdb": _c(busy_time=0)},
            {"sda": _c(busy_time=100), "sdb": _c(busy_time=850)},  # 85%
        )
        result = IOStrainChecker().check()
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.metrics["busiest_disk"], "sdb")
        self.assertIn("sdb", result.message)

    def test_critical_util(self):
        self._patch({"sda": _c(busy_time=0)}, {"sda": _c(busy_time=970)})  # 97%
        result = IOStrainChecker().check()
        self.assertEqual(result.status, CheckStatus.CRITICAL)

    def test_non_linux_skips_ok(self):
        self._start(mock.patch.object(sys, "platform", "darwin"))
        result = IOStrainChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["disk_count"], 0)

    def test_no_counters_skips_ok(self):
        self._start(mock.patch.object(sys, "platform", "linux"))
        self._start(
            mock.patch(
                "apps.checkers.checkers.io_strain.psutil.disk_io_counters",
                return_value=None,
            )
        )
        result = IOStrainChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("no disk IO counters", result.message)

    def test_no_busy_time_skips_ok(self):
        self._patch({"sda": _c(busy_time=None)}, {"sda": _c(busy_time=None)})
        result = IOStrainChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("no disk IO utilization data", result.message)

    def test_invalid_sample_interval_raises(self):
        with self.assertRaises(ValueError):
            IOStrainChecker(sample_interval=0)

    def test_registered_in_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers import IOStrainChecker as Exported

        self.assertIs(CHECKER_REGISTRY["io_strain"], Exported)
