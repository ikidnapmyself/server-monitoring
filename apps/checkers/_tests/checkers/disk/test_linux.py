"""Tests for the Linux disk analysis checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus


class DiskLinuxCheckerTests(TestCase):
    """Tests for DiskLinuxChecker."""

    def _get_checker_class(self):
        from apps.checkers.checkers.disk.linux import DiskLinuxChecker

        return DiskLinuxChecker

    @patch("apps.checkers.checkers.disk.base.sys")
    @patch("apps.checkers.checkers.disk.linux.sys")
    def test_skips_on_macos(self, mock_sys, mock_base_sys):
        """Checker returns OK skip on non-linux platforms."""
        mock_sys.platform = "darwin"
        mock_base_sys.platform = "darwin"
        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("skipped", result.message.lower())
        self.assertEqual(result.metrics.get("platform"), "darwin")

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
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

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
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

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
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
        self.assertTrue(any("apt" in line.lower() for r in recs for line in r))

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
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

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
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

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    def test_space_hogs_globally_sorted_across_scan_targets(self, mock_old, mock_scan, mock_sys):
        """space_hogs is sorted desc across the four Linux scan targets."""
        mock_sys.platform = "linux"

        def fake_scan(path, timeout=None):
            if path == "/var/cache/apt/archives":
                return [{"path": f"{path}/small", "size_mb": 50.0}]
            if path == "/var/log/journal":
                return [{"path": f"{path}/big", "size_mb": 900.0}]
            if path == "/var/lib/docker":
                return [{"path": f"{path}/medium", "size_mb": 300.0}]
            return []

        mock_scan.side_effect = fake_scan
        mock_old.return_value = []

        checker = self._get_checker_class()()
        result = checker.check()

        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 900.0)

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_large_files")
    def test_walks_srv_and_opt_for_large_files(self, mock_large, _old, _scan, mock_sys):
        """Linux large_file_targets includes /srv and /opt."""
        mock_sys.platform = "linux"
        mock_large.return_value = [
            {"path": "/srv/data/big.bin", "size_mb": 2000.0},
            {"path": "/opt/app/lib.so", "size_mb": 150.0},
        ]
        result = self._get_checker_class()().check()
        sizes = [item["size_mb"] for item in result.metrics["large_files"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 2000.0)


class DiskLinuxBuildRecommendationsTests(TestCase):
    """Tests for DiskLinuxChecker._build_recommendations() branch coverage."""

    def _make_checker(self):
        from apps.checkers.checkers.disk.linux import DiskLinuxChecker

        return DiskLinuxChecker()

    def test_apt_cache_recommendation(self):
        """Recommends apt clean when apt appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/var/cache/apt/archives", "size_mb": 1500.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("apt clean" in line for r in recs for line in r))

    def test_journal_logs_recommendation(self):
        """Recommends journalctl vacuum when journal appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/var/log/journal", "size_mb": 800.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("journalctl" in line for r in recs for line in r))

    def test_docker_recommendation(self):
        """Recommends docker system prune when docker appears in space_hogs."""
        checker = self._make_checker()
        space_hogs = [{"path": "/var/lib/docker/overlay2", "size_mb": 5000.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("docker system prune" in line for r in recs for line in r))

    def test_snap_cache_recommendation(self):
        """Recommends removing old snap revisions when snap appears."""
        checker = self._make_checker()
        space_hogs = [{"path": "/var/lib/snapd/snaps", "size_mb": 2000.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("snap" in line.lower() for r in recs for line in r))

    def test_old_files_recommendation(self):
        """Recommends removing old temp files when old_files is non-empty."""
        checker = self._make_checker()
        old_files = [{"path": "/tmp/old-build", "size_mb": 100.0, "age_days": 14}]
        recs = checker._build_recommendations([], old_files, [])
        self.assertTrue(any("/tmp" in line for r in recs for line in r))

    def test_no_matches_empty_recommendations(self):
        """Returns empty list when no patterns match and no old files."""
        checker = self._make_checker()
        space_hogs = [{"path": "/some/unknown/path", "size_mb": 100.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertEqual(recs, [])

    def test_jetbrains_recommendation(self):
        from apps.checkers.checkers.disk.linux import DiskLinuxChecker

        checker = DiskLinuxChecker()
        space_hogs = [{"path": "/home/me/.cache/JetBrains/PyCharm", "size_mb": 2000.0}]
        recs = checker._build_recommendations(space_hogs, [], [])
        self.assertTrue(any("Invalidate Caches" in line for r in recs for line in r))


class DiskLinuxCoverageGapTests(TestCase):
    """Tests covering remaining branch gaps in disk/linux.py."""

    def _get_checker_class(self):
        from apps.checkers.checkers.disk.linux import DiskLinuxChecker

        return DiskLinuxChecker

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    def test_old_file_duplicate_path_skipped(self, mock_old, mock_scan, mock_sys):
        """Old file with same path as a space_hog is skipped (seen set)."""
        mock_sys.platform = "linux"
        shared_path = "/var/cache/apt/archives"
        mock_scan.return_value = [{"path": shared_path, "size_mb": 100.0}]
        mock_old.return_value = [{"path": shared_path, "size_mb": 100.0, "age_days": 10}]

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(len(result.metrics["old_files"]), 0)

    @patch("apps.checkers.checkers.disk.linux.sys")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_catch_all_exception(self, mock_scan, mock_sys):
        """Unexpected exception in check() returns UNKNOWN error result."""
        mock_sys.platform = "linux"
        mock_scan.side_effect = RuntimeError("unexpected")

        checker = self._get_checker_class()()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("unexpected", result.message)
