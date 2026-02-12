"""Tests for the macOS disk analysis checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus


class DiskMacOSCheckerTests(TestCase):
    """Tests for DiskMacOSChecker."""

    def _get_checker_class(self):
        from apps.checkers.checkers.disk_macos import DiskMacOSChecker

        return DiskMacOSChecker

    @patch("apps.checkers.checkers.disk_macos.sys")
    def test_skips_on_linux(self, mock_sys):
        """Checker returns OK skip on non-darwin platforms."""
        mock_sys.platform = "linux"
        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("skipped", result.message.lower())
        self.assertEqual(result.metrics.get("platform"), "linux")

    @patch("apps.checkers.checkers.disk_macos.sys")
    @patch("apps.checkers.checkers.disk_macos.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._scan_directory")
    def test_scans_library_caches(self, mock_scan, mock_expanduser, mock_sys):
        """Checker scans ~/Library/Caches on macOS."""
        mock_sys.platform = "darwin"
        mock_expanduser.side_effect = lambda p: p.replace("~", "/Users/testuser")
        mock_scan.return_value = [
            {"path": "/Users/testuser/Library/Caches/com.apple.Safari", "size_mb": 512.0},
            {"path": "/Users/testuser/Library/Caches/com.spotify.client", "size_mb": 256.0},
        ]

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("space_hogs", result.metrics)
        self.assertGreater(len(result.metrics["space_hogs"]), 0)

    @patch("apps.checkers.checkers.disk_macos.sys")
    @patch("apps.checkers.checkers.disk_macos.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._scan_directory")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._find_old_files")
    def test_warning_when_recoverable_exceeds_threshold(
        self, mock_old_files, mock_scan, mock_expanduser, mock_sys
    ):
        """Checker returns WARNING when total recoverable exceeds warning threshold."""
        mock_sys.platform = "darwin"
        mock_expanduser.side_effect = lambda p: p.replace("~", "/Users/testuser")
        mock_scan.return_value = [
            {"path": "/Users/testuser/Library/Caches/big-app", "size_mb": 6000.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()(warning_threshold=5000.0, critical_threshold=20000.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertGreaterEqual(result.metrics["total_recoverable_mb"], 5000.0)

    @patch("apps.checkers.checkers.disk_macos.sys")
    @patch("apps.checkers.checkers.disk_macos.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._scan_directory")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._find_old_files")
    def test_critical_when_recoverable_exceeds_critical(
        self, mock_old_files, mock_scan, mock_expanduser, mock_sys
    ):
        """Checker returns CRITICAL above critical threshold."""
        mock_sys.platform = "darwin"
        mock_expanduser.side_effect = lambda p: p.replace("~", "/Users/testuser")
        mock_scan.return_value = [
            {"path": "/Users/testuser/Library/Developer/Xcode/DerivedData", "size_mb": 25000.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()(warning_threshold=5000.0, critical_threshold=20000.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.disk_macos.sys")
    @patch("apps.checkers.checkers.disk_macos.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._scan_directory")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._find_old_files")
    def test_includes_recommendations(self, mock_old_files, mock_scan, mock_expanduser, mock_sys):
        """Checker includes cleanup recommendations."""
        mock_sys.platform = "darwin"
        mock_expanduser.side_effect = lambda p: p.replace("~", "/Users/testuser")
        mock_scan.return_value = [
            {"path": "/Users/testuser/Library/Caches/Homebrew", "size_mb": 2000.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("recommendations", result.metrics)
        self.assertIsInstance(result.metrics["recommendations"], list)

    @patch("apps.checkers.checkers.disk_macos.sys")
    @patch("apps.checkers.checkers.disk_macos.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._scan_directory")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._find_old_files")
    def test_ok_when_below_thresholds(self, mock_old_files, mock_scan, mock_expanduser, mock_sys):
        """Checker returns OK when recoverable is below warning threshold."""
        mock_sys.platform = "darwin"
        mock_expanduser.side_effect = lambda p: p.replace("~", "/Users/testuser")
        mock_scan.return_value = [
            {"path": "/Users/testuser/Library/Caches/small-app", "size_mb": 100.0},
        ]
        mock_old_files.return_value = []

        checker = self._get_checker_class()(warning_threshold=5000.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)

    @patch("apps.checkers.checkers.disk_macos.sys")
    @patch("apps.checkers.checkers.disk_macos.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._scan_directory")
    @patch("apps.checkers.checkers.disk_macos.DiskMacOSChecker._find_old_files")
    def test_old_files_in_downloads(self, mock_old_files, mock_scan, mock_expanduser, mock_sys):
        """Checker finds old files in ~/Downloads."""
        mock_sys.platform = "darwin"
        mock_expanduser.side_effect = lambda p: p.replace("~", "/Users/testuser")
        mock_scan.return_value = []
        mock_old_files.return_value = [
            {"path": "/Users/testuser/Downloads/old-archive.zip", "size_mb": 500.0, "age_days": 90},
        ]

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertIn("old_files", result.metrics)
        self.assertEqual(len(result.metrics["old_files"]), 1)
        self.assertEqual(result.metrics["old_files"][0]["age_days"], 90)
