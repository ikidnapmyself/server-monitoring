"""Direct unit tests for BaseDiskAnalyzer using a stub subclass."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.disk.base import BaseDiskAnalyzer


class _StubAnalyzer(BaseDiskAnalyzer):
    """Test-only concrete subclass with deterministic config."""

    name = "_stub"
    scan_targets = ["/test/scan"]
    old_file_targets = ["/test/old"]
    large_file_targets = ["/test/large"]
    old_max_age_days = 7
    recommendation_rules = [(["match_keyword"], "matched advice")]
    old_files_advice = "old advice"
    large_files_advice = "large advice"

    def _is_applicable(self) -> bool:
        return True


class _NonApplicableAnalyzer(_StubAnalyzer):
    name = "_nonapplicable"

    def _is_applicable(self) -> bool:
        return False


class BaseDiskAnalyzerTests(TestCase):
    """Direct tests of BaseDiskAnalyzer.check()."""

    def test_skips_when_not_applicable(self):
        result = _NonApplicableAnalyzer().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("Skipped", result.message)
        self.assertIn("platform", result.metrics)
        self.assertNotIn("space_hogs", result.metrics)

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_collects_all_three_lists(self, mock_scan, mock_old, mock_large):
        mock_scan.return_value = [{"path": "/test/scan/a", "size_mb": 100.0}]
        mock_old.return_value = [{"path": "/test/old/b", "size_mb": 50.0, "age_days": 10}]
        mock_large.return_value = [{"path": "/test/large/c", "size_mb": 200.0}]

        result = _StubAnalyzer().check()

        self.assertEqual(len(result.metrics["space_hogs"]), 1)
        self.assertEqual(len(result.metrics["old_files"]), 1)
        self.assertEqual(len(result.metrics["large_files"]), 1)
        self.assertAlmostEqual(result.metrics["total_recoverable_mb"], 350.0)

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_dedup_across_lists(self, mock_scan, mock_old, mock_large):
        mock_scan.return_value = [{"path": "/dup", "size_mb": 100.0}]
        mock_old.return_value = [{"path": "/dup", "size_mb": 100.0, "age_days": 5}]
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertEqual(len(result.metrics["space_hogs"]), 1)
        self.assertEqual(len(result.metrics["old_files"]), 0)

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_recommendation_rule_matches_keyword(self, mock_scan, mock_old, mock_large):
        mock_scan.return_value = [{"path": "/test/scan/match_keyword/x", "size_mb": 10.0}]
        mock_old.return_value = []
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertIn("matched advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_recommendation_rule_does_not_match(self, mock_scan, mock_old, mock_large):
        mock_scan.return_value = [{"path": "/test/scan/other/x", "size_mb": 10.0}]
        mock_old.return_value = []
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertNotIn("matched advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_old_files_advice_appears_when_old_files_present(self, mock_scan, mock_old, mock_large):
        mock_scan.return_value = []
        mock_old.return_value = [{"path": "/test/old/x", "size_mb": 10.0, "age_days": 30}]
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertIn("old advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_large_files_advice_appears_when_large_files_present(
        self, mock_scan, mock_old, mock_large
    ):
        mock_scan.return_value = []
        mock_old.return_value = []
        mock_large.return_value = [{"path": "/test/large/x", "size_mb": 200.0}]

        result = _StubAnalyzer().check()

        self.assertIn("large advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.find_large_files")
    @patch("apps.checkers.checkers.disk.base.find_old_files")
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_advice_omitted_when_section_empty(self, mock_scan, mock_old, mock_large):
        mock_scan.return_value = []
        mock_old.return_value = []
        mock_large.return_value = []

        result = _StubAnalyzer().check()

        self.assertNotIn("old advice", result.metrics["recommendations"])
        self.assertNotIn("large advice", result.metrics["recommendations"])

    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_scanner_exception_returns_unknown(self, mock_scan):
        mock_scan.side_effect = OSError("boom")

        result = _StubAnalyzer().check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("boom", result.message)

    @patch("apps.checkers.checkers.disk.base.find_large_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_lists_globally_sorted_desc(self, mock_scan, _old, _large):
        mock_scan.return_value = [
            {"path": "/a", "size_mb": 5.0},
            {"path": "/b", "size_mb": 100.0},
        ]

        result = _StubAnalyzer().check()
        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class _MultiTargetStub(BaseDiskAnalyzer):
    """Stub with multiple scan targets to exercise cross-target sort."""

    name = "_multi"
    scan_targets = ["/first", "/second"]
    old_file_targets = []
    large_file_targets = []
    old_max_age_days = 7

    def _is_applicable(self) -> bool:
        return True


class MultiTargetSortTests(TestCase):
    @patch("apps.checkers.checkers.disk.base.find_large_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.find_old_files", return_value=[])
    @patch("apps.checkers.checkers.disk.base.scan_directory")
    def test_globally_sorts_across_multiple_targets(self, mock_scan, _old, _large):
        def fake_scan(path, timeout=None):
            if path == "/first":
                return [{"path": "/first/small", "size_mb": 5.0}]
            if path == "/second":
                return [{"path": "/second/big", "size_mb": 500.0}]
            return []

        mock_scan.side_effect = fake_scan

        result = _MultiTargetStub().check()
        sizes = [item["size_mb"] for item in result.metrics["space_hogs"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 500.0)
