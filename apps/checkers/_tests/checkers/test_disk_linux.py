"""Tests for the Linux disk analysis checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus


class DiskLinuxCheckerTests(TestCase):
    """Tests for DiskLinuxChecker."""

    def _get_checker_class(self):
        from apps.checkers.checkers.disk_linux import DiskLinuxChecker

        return DiskLinuxChecker

    @patch("apps.checkers.checkers.disk_linux.sys")
    def test_skips_on_macos(self, mock_sys):
        """Checker returns OK skip on non-linux platforms."""
        mock_sys.platform = "darwin"
        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("skipped", result.message.lower())
        self.assertEqual(result.metrics.get("platform"), "darwin")

    @patch("apps.checkers.checkers.disk_linux.sys")
    @patch("apps.checkers.checkers.disk_linux.scan_directory")
    @patch("apps.checkers.checkers.disk_linux.find_old_files")
    def test_scans_apt_cache(self, mock_old_files, mock_scan, mock_sys):
        """Checker scans /var/cache/apt/archives on Linux."""
        mock_sys.platform = "linux"
        mock_scan.return_value = [
            {"path": "/var/cache/apt/archives", "size_mb": 1500.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("space_hogs", result.metrics)

    @patch("apps.checkers.checkers.disk_linux.sys")
    @patch("apps.checkers.checkers.disk_linux.scan_directory")
    @patch("apps.checkers.checkers.disk_linux.find_old_files")
    def test_warning_above_threshold(self, mock_old_files, mock_scan, mock_sys):
        """Checker returns WARNING when recoverable exceeds threshold."""
        mock_sys.platform = "linux"
        mock_scan.return_value = [
            {"path": "/var/lib/docker", "size_mb": 8000.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()(warning_threshold=5000.0, critical_threshold=20000.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.disk_linux.sys")
    @patch("apps.checkers.checkers.disk_linux.scan_directory")
    @patch("apps.checkers.checkers.disk_linux.find_old_files")
    def test_includes_linux_recommendations(self, mock_old_files, mock_scan, mock_sys):
        """Checker includes Linux-specific cleanup recommendations."""
        mock_sys.platform = "linux"
        mock_scan.return_value = [
            {"path": "/var/cache/apt/archives", "size_mb": 2000.0},
            {"path": "/var/log/journal", "size_mb": 1000.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("recommendations", result.metrics)
        recs = result.metrics["recommendations"]
        self.assertTrue(any("apt" in r.lower() for r in recs))

    @patch("apps.checkers.checkers.disk_linux.sys")
    @patch("apps.checkers.checkers.disk_linux.scan_directory")
    @patch("apps.checkers.checkers.disk_linux.find_old_files")
    def test_old_tmp_files(self, mock_old_files, mock_scan, mock_sys):
        """Checker finds old files in /tmp."""
        mock_sys.platform = "linux"
        mock_scan.return_value = []
        mock_old_files.return_value = [
            {"path": "/tmp/old-build-artifact.tar.gz", "size_mb": 200.0, "age_days": 14},
        ]

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("old_files", result.metrics)
        self.assertEqual(len(result.metrics["old_files"]), 1)

    @patch("apps.checkers.checkers.disk_linux.sys")
    @patch("apps.checkers.checkers.disk_linux.scan_directory")
    @patch("apps.checkers.checkers.disk_linux.find_old_files")
    def test_ok_below_thresholds(self, mock_old_files, mock_scan, mock_sys):
        """Checker returns OK when everything is clean."""
        mock_sys.platform = "linux"
        mock_scan.return_value = [
            {"path": "/var/cache/apt/archives", "size_mb": 50.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()(warning_threshold=5000.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
