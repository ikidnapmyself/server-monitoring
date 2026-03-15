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


class DiskCommonBuildRecommendationsTests(TestCase):
    """Tests for DiskCommonChecker._build_recommendations() branch coverage."""

    def _make_checker(self):
        from apps.checkers.checkers.disk_common import DiskCommonChecker

        return DiskCommonChecker()

    def test_var_log_recommendation(self):
        """Recommends log rotation when /var/log appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/var/log/syslog", "size_mb": 500.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("/var/log" in r for r in recs))

    def test_pip_cache_recommendation(self):
        """Recommends pip cache purge when pip appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/home/user/.cache/pip/wheels", "size_mb": 300.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("pip cache purge" in r for r in recs))

    def test_npm_cache_recommendation(self):
        """Recommends npm cache clean when npm appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/home/user/.npm/_cacache", "size_mb": 200.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("npm cache clean" in r for r in recs))

    def test_dot_npm_cache_recommendation(self):
        """Recommends npm cache clean when .npm appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/home/user/.npm", "size_mb": 200.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("npm cache clean" in r for r in recs))

    def test_dot_cache_recommendation(self):
        """Recommends clearing ~/.cache when .cache appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/home/user/.cache/thumbnails", "size_mb": 150.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("~/.cache" in r for r in recs))

    def test_old_files_recommendation(self):
        """Recommends removing old temp files when old_files is non-empty."""
        checker = self._make_checker()
        old_files = [{"path": "/tmp/stale", "size_mb": 50.0, "age_days": 30}]
        recs = checker._build_recommendations([], old_files, [])
        self.assertTrue(any("/tmp" in r for r in recs))

    def test_large_files_recommendation(self):
        """Recommends reviewing large files when large_files is non-empty."""
        checker = self._make_checker()
        large_files = [{"path": "/home/user/bigfile.tar", "size_mb": 5000.0}]
        recs = checker._build_recommendations([], [], large_files)
        self.assertTrue(any("large files" in r.lower() for r in recs))

    def test_no_matches_empty_recommendations(self):
        """Returns empty list when no patterns match and no old/large files."""
        checker = self._make_checker()
        space_hogs = [{"path": "/some/unknown/path", "size_mb": 100.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertEqual(recs, [])


class DiskCommonCoverageGapTests(TestCase):
    """Tests covering remaining branch gaps in disk_common.py."""

    def _get_checker_class(self):
        from apps.checkers.checkers.disk_common import DiskCommonChecker

        return DiskCommonChecker

    @patch("apps.checkers.checkers.disk_common.os.name", "nt")
    def test_skips_on_non_posix(self):
        """Returns OK skip when os.name is not 'posix' (e.g., Windows)."""
        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("skipped", result.message.lower())

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    @patch("apps.checkers.checkers.disk_common.find_old_files")
    @patch("apps.checkers.checkers.disk_common.find_large_files")
    def test_large_file_duplicate_path_skipped(
        self, mock_large, mock_old, mock_scan, mock_expanduser
    ):
        """Large file with same path as a space_hog is skipped (seen set)."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        shared_path = "/home/testuser/.cache/bigfile.bin"
        mock_scan.return_value = [{"path": shared_path, "size_mb": 100.0}]
        mock_old.return_value = []
        mock_large.return_value = [{"path": shared_path, "size_mb": 100.0}]

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(len(result.metrics["large_files"]), 0)

    @patch("apps.checkers.checkers.disk_common.os.path.expanduser")
    @patch("apps.checkers.checkers.disk_common.scan_directory")
    def test_catch_all_exception(self, mock_scan, mock_expanduser):
        """Unexpected exception in check() returns UNKNOWN error result."""
        mock_expanduser.side_effect = lambda p: p.replace("~", "/home/testuser")
        mock_scan.side_effect = RuntimeError("unexpected")

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("unexpected", result.message)
