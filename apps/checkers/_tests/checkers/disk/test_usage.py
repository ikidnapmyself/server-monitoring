"""Tests for the disk checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers._tests.checkers.test_describe_contract import RAW
from apps.checkers.checkers import CheckStatus, DiskChecker


class DiskCheckerTests(TestCase):
    """Tests for the DiskChecker."""

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_ok(self, mock_psutil):
        mock_usage = MagicMock()
        mock_usage.percent = 60.0
        mock_usage.total = 500 * 1024**3  # 500 GB
        mock_usage.used = 300 * 1024**3
        mock_usage.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_usage

        checker = DiskChecker(paths=["/"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["worst_percent"], 60.0)

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_multiple_paths(self, mock_psutil):
        def disk_usage_side_effect(path):
            mock = MagicMock()
            if path == "/":
                mock.percent = 50.0
            elif path == "/data":
                mock.percent = 95.0  # Critical
            mock.total = 500 * 1024**3
            mock.used = mock.percent / 100 * mock.total
            mock.free = mock.total - mock.used
            return mock

        mock_psutil.disk_usage.side_effect = disk_usage_side_effect

        checker = DiskChecker(paths=["/", "/data"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertEqual(result.metrics["worst_path"], "/data")

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_file_not_found_returns_unknown(self, mock_psutil):
        """FileNotFoundError for a path sets UNKNOWN status."""
        mock_psutil.disk_usage.side_effect = FileNotFoundError("not found")

        checker = DiskChecker(paths=["/nonexistent"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("not accessible", result.message)
        self.assertIn("/nonexistent", result.message)
        self.assertEqual(
            result.metrics["disks"]["/nonexistent"],
            {"error": "not found"},
        )

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_permission_error_returns_unknown(self, mock_psutil):
        """PermissionError for a path sets UNKNOWN status instead of aborting."""
        mock_psutil.disk_usage.side_effect = PermissionError("permission denied")

        checker = DiskChecker(paths=["/protected"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("not accessible", result.message)
        self.assertEqual(
            result.metrics["disks"]["/protected"],
            {"error": "permission denied"},
        )

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_unknown_not_overwritten_by_valid_path(self, mock_psutil):
        """UNKNOWN from a missing path is not overwritten by a subsequent valid path."""

        def disk_usage_side_effect(path):
            if path == "/nonexistent":
                raise FileNotFoundError("not found")
            mock = MagicMock()
            mock.percent = 60.0
            mock.total = 500 * 1024**3
            mock.used = 300 * 1024**3
            mock.free = 200 * 1024**3
            return mock

        mock_psutil.disk_usage.side_effect = disk_usage_side_effect

        checker = DiskChecker(paths=["/nonexistent", "/"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("/nonexistent", result.message)

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_first_error_path_reported_when_multiple_fail(self, mock_psutil):
        """When multiple paths fail, only the first error path is reported."""
        mock_psutil.disk_usage.side_effect = FileNotFoundError("not found")

        checker = DiskChecker(paths=["/first-missing", "/second-missing"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("/first-missing", result.message)
        self.assertNotIn("/second-missing", result.message)

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_second_path_lower_usage_does_not_override_worst(self, mock_psutil):
        """When a second path has lower usage than the first, worst stays with the first."""

        def disk_usage_side_effect(path):
            mock = MagicMock()
            if path == "/":
                mock.percent = 85.0
            elif path == "/home":
                mock.percent = 40.0  # Lower than first
            mock.total = 500 * 1024**3
            mock.used = mock.percent / 100 * mock.total
            mock.free = mock.total - mock.used
            return mock

        mock_psutil.disk_usage.side_effect = disk_usage_side_effect

        checker = DiskChecker(paths=["/", "/home"])
        result = checker.check()

        self.assertEqual(result.metrics["worst_path"], "/")
        self.assertEqual(result.metrics["worst_percent"], 85.0)

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_disk_check_catch_all_exception(self, mock_psutil):
        """Unexpected exception in check() returns UNKNOWN error result."""
        mock_psutil.disk_usage.side_effect = RuntimeError("unexpected")

        checker = DiskChecker(paths=["/"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("unexpected", result.message)


class DiskDescribeTests(TestCase):
    def _metrics(self, percent, free_gb):
        disk = {"percent": percent, "total_gb": 500.0, "used_gb": 1.0, "free_gb": free_gb}
        return {"disks": {"/": disk}, "worst_percent": percent, "worst_path": "/"}

    def test_ok_names_the_fullest_disk(self):
        d = DiskChecker().describe(CheckStatus.OK, self._metrics(41.0, 120.0))
        self.assertEqual(d.headline, "Fullest disk / at 41%, 120.0 GB free")
        self.assertIsNone(d.action)

    def test_critical_says_what_to_free(self):
        d = DiskChecker().describe(CheckStatus.CRITICAL, self._metrics(94.0, 3.2))
        self.assertEqual(d.headline, "/ is 94% full, 3.2 GB free")
        self.assertEqual(
            d.action.text,
            "Free space on /: clear old logs, caches and packages, or grow the volume.",
        )

    @patch("apps.checkers.checkers.disk.usage.psutil")
    def test_real_output_reads_in_words(self, mock_psutil):
        mock_usage = MagicMock()
        mock_usage.percent = 96.0
        mock_usage.total = 500 * 1024**3
        mock_usage.used = 480 * 1024**3
        mock_usage.free = 20 * 1024**3
        mock_psutil.disk_usage.return_value = mock_usage
        result = DiskChecker(paths=["/var/log"]).check()
        d = DiskChecker().describe(result.status, result.metrics, result.message)
        self.assertEqual(d.headline, "/var/log is 96% full, 20.0 GB free")
        self.assertIsNone(RAW.search(d.headline))
