"""Tests for the disk checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers import CheckStatus, DiskChecker


class DiskCheckerTests(TestCase):
    """Tests for the DiskChecker."""

    @patch("apps.checkers.checkers.disk.psutil")
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

    @patch("apps.checkers.checkers.disk.psutil")
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

    @patch("apps.checkers.checkers.disk.psutil")
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

    @patch("apps.checkers.checkers.disk.psutil")
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

    @patch("apps.checkers.checkers.disk.psutil")
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

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_first_error_path_reported_when_multiple_fail(self, mock_psutil):
        """When multiple paths fail, only the first error path is reported."""
        mock_psutil.disk_usage.side_effect = FileNotFoundError("not found")

        checker = DiskChecker(paths=["/first-missing", "/second-missing"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("/first-missing", result.message)
        self.assertNotIn("/second-missing", result.message)
