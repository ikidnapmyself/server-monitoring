"""Tests for the disk inode-usage checker."""

from collections import namedtuple
from unittest import mock

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.disk_inodes import DiskInodesChecker

# Minimal os.statvfs_result subset (only the fields the checker reads).
svfs = namedtuple("svfs", ["f_files", "f_ffree"])


def _patch_statvfs(mapping):
    """Patch os.statvfs to look results up by path from ``mapping``."""

    def fake(path):
        result = mapping[path]
        if isinstance(result, Exception):
            raise result
        return result

    return mock.patch("apps.checkers.checkers.disk_inodes.os.statvfs", side_effect=fake)


class DiskInodesCheckerTests(TestCase):
    def test_low_usage_is_ok(self):
        with _patch_statvfs({"/": svfs(f_files=1000, f_ffree=900)}):  # 10%
            result = DiskInodesChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["worst_path"], "/")
        self.assertEqual(result.metrics["worst_percent"], 10.0)
        self.assertEqual(result.metrics["filesystems"]["/"]["used"], 100)

    def test_warning_usage(self):
        with _patch_statvfs({"/": svfs(f_files=1000, f_ffree=150)}):  # 85%
            result = DiskInodesChecker().check()
        self.assertEqual(result.status, CheckStatus.WARNING)

    def test_critical_usage(self):
        with _patch_statvfs({"/": svfs(f_files=1000, f_ffree=30)}):  # 97%
            result = DiskInodesChecker().check()
        self.assertEqual(result.status, CheckStatus.CRITICAL)

    def test_worst_path_drives_status(self):
        mapping = {
            "/": svfs(f_files=1000, f_ffree=900),  # 10%
            "/data": svfs(f_files=1000, f_ffree=40),  # 96%
        }
        with _patch_statvfs(mapping):
            result = DiskInodesChecker(paths=["/", "/data"]).check()
        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertEqual(result.metrics["worst_path"], "/data")

    def test_zero_usage_still_sets_worst_path(self):
        # A genuinely 0%-used filesystem must be reported, not mistaken for
        # "no inode-tracking filesystems".
        with _patch_statvfs({"/": svfs(f_files=1000, f_ffree=1000)}):
            result = DiskInodesChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["worst_path"], "/")
        self.assertIn("/ at 0.0%", result.message)

    def test_filesystem_without_inodes_is_skipped(self):
        with _patch_statvfs({"/": svfs(f_files=0, f_ffree=0)}):
            result = DiskInodesChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("no inode-tracking filesystems", result.message)
        self.assertFalse(result.metrics["filesystems"]["/"]["inodes_supported"])

    def test_inaccessible_path_is_unknown(self):
        with _patch_statvfs({"/nope": FileNotFoundError("no such path")}):
            result = DiskInodesChecker(paths=["/nope"]).check()
        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("/nope", result.message)

    def test_only_first_error_path_is_reported(self):
        mapping = {
            "/a": PermissionError("denied"),
            "/b": FileNotFoundError("gone"),
        }
        with _patch_statvfs(mapping):
            result = DiskInodesChecker(paths=["/a", "/b"]).check()
        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("/a", result.message)  # first error path wins the message
        self.assertIn("/b", result.metrics["filesystems"])

    def test_lower_later_path_does_not_override_worst(self):
        # A valid path with lower usage after the worst one must not override it.
        mapping = {
            "/data": svfs(f_files=1000, f_ffree=40),  # 96% (worst, first)
            "/": svfs(f_files=1000, f_ffree=900),  # 10% (lower, later)
        }
        with _patch_statvfs(mapping):
            result = DiskInodesChecker(paths=["/data", "/"]).check()
        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertEqual(result.metrics["worst_path"], "/data")

    def test_statvfs_unavailable_skips_ok(self):
        fake_os = mock.Mock(spec=[])  # no statvfs attribute
        with mock.patch("apps.checkers.checkers.disk_inodes.os", fake_os):
            result = DiskInodesChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("statvfs unavailable", result.message)

    def test_registered_in_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers import DiskInodesChecker as Exported

        self.assertIs(CHECKER_REGISTRY["disk_inodes"], Exported)
