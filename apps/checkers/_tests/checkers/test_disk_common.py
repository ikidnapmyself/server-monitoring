"""Tests for the cross-platform disk analysis checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus


class DiskCommonCheckerTests(TestCase):
    """Tests for DiskCommonChecker."""

    def _get_checker_class(self):
        from apps.checkers.checkers.disk_common import DiskCommonChecker

        return DiskCommonChecker

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_scans_var_log(self, mock_large, mock_old, mock_scan, mock_expanduser):
        """Checker scans /var/log."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = [
            {"path": "/var/log/syslog", "size_mb": 800.0},
        ]
        mock_old.return_value = []
        mock_large.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("space_hogs", result.metrics)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_finds_large_files_in_home(self, mock_large, mock_old, mock_scan, mock_expanduser):
        """Checker finds large files in the home directory."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = []
        mock_old.return_value = []
        mock_large.return_value = [
            {"path": "/home/testuser/backup.tar.gz", "size_mb": 5000.0},
        ]

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("large_files", result.metrics)
        self.assertEqual(len(result.metrics["large_files"]), 1)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_warning_above_threshold(self, mock_large, mock_old, mock_scan, mock_expanduser):
        """Returns WARNING when total recoverable exceeds a threshold."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = [
            {"path": "/var/log", "size_mb": 3000.0},
            {"path": "/home/testuser/.cache", "size_mb": 3000.0},
        ]
        mock_old.return_value = []
        mock_large.return_value = []

        checker = self._get_checker_class()(warning_threshold=5000.0, critical_threshold=20000.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_includes_recommendations(self, mock_large, mock_old, mock_scan, mock_expanduser):
        """Includes cleanup recommendations."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = [
            {"path": "/home/testuser/.cache/pip", "size_mb": 1500.0},
        ]
        mock_old.return_value = []
        mock_large.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("recommendations", result.metrics)
        self.assertIsInstance(result.metrics["recommendations"], list)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_old_temp_files(self, mock_large, mock_old, mock_scan, mock_expanduser):
        """Finds old files in /tmp and /var/tmp."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = []
        mock_old.return_value = [
            {"path": "/tmp/stale-session-12345", "size_mb": 50.0, "age_days": 45},
        ]
        mock_large.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("old_files", result.metrics)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_ok_when_clean(self, mock_large, mock_old, mock_scan, mock_expanduser):
        """Returns OK when nothing significant found."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = []
        mock_old.return_value = []
        mock_large.return_value = []

        checker = self._get_checker_class()(warning_threshold=5000.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_metrics_include_platform(self, mock_large, mock_old, mock_scan, mock_expanduser):
        """Metrics always include platform info."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.return_value = []
        mock_old.return_value = []
        mock_large.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("platform", result.metrics)
