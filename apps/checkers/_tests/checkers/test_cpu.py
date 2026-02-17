"""Tests for the CPU checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers import CheckStatus, CPUChecker


class CPUCheckerInitTests(TestCase):
    """Tests for CPUChecker initialization."""

    def test_default_samples(self):
        checker = CPUChecker()
        self.assertEqual(checker.samples, 5)

    def test_default_sample_interval(self):
        checker = CPUChecker()
        self.assertEqual(checker.sample_interval, 1.0)

    def test_custom_samples(self):
        checker = CPUChecker(samples=10, sample_interval=0.5)
        self.assertEqual(checker.samples, 10)
        self.assertEqual(checker.sample_interval, 0.5)


class CPUCheckerTests(TestCase):
    """Tests for CPUChecker.check() multi-sample behavior."""

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_averages_multiple_samples(self, mock_psutil):
        """Average of [20, 40, 60] = 40 -> OK."""
        mock_psutil.cpu_percent.side_effect = [20.0, 40.0, 60.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertAlmostEqual(result.metrics["cpu_percent"], 40.0)
        self.assertAlmostEqual(result.metrics["cpu_min"], 20.0)
        self.assertAlmostEqual(result.metrics["cpu_max"], 60.0)
        self.assertEqual(result.metrics["samples"], 3)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_status_from_average_ok(self, mock_psutil):
        """Average below warning threshold -> OK."""
        mock_psutil.cpu_percent.side_effect = [30.0, 40.0, 50.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_status_from_average_warning(self, mock_psutil):
        """Average at 75 -> WARNING (threshold 70)."""
        mock_psutil.cpu_percent.side_effect = [70.0, 75.0, 80.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_status_from_average_critical(self, mock_psutil):
        """Average at 95 -> CRITICAL (threshold 90)."""
        mock_psutil.cpu_percent.side_effect = [90.0, 95.0, 100.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_message_shows_average(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = [20.0, 40.0, 60.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertIn("40.0%", result.message)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_metrics_include_cpu_count(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = [50.0]
        mock_psutil.cpu_count.return_value = 8

        checker = CPUChecker(samples=1, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.metrics["cpu_count"], 8)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_per_cpu_averages_across_samples(self, mock_psutil):
        """Per-CPU mode: averages each core across samples."""
        # 2 samples, 4 cores each
        mock_psutil.cpu_percent.side_effect = [
            [10.0, 20.0, 80.0, 40.0],  # sample 1
            [30.0, 40.0, 60.0, 20.0],  # sample 2
        ]

        checker = CPUChecker(samples=2, sample_interval=0.0, per_cpu=True)
        result = checker.check()

        # Per-core averages: [20, 30, 70, 30]
        # Max per-core avg = 70 -> WARNING
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertAlmostEqual(result.metrics["cpu_percent"], 70.0)
        self.assertEqual(result.metrics["per_cpu_percent"], [20.0, 30.0, 70.0, 30.0])
        self.assertEqual(result.metrics["cpu_count"], 4)
        self.assertAlmostEqual(result.metrics["cpu_min"], 60.0)
        self.assertAlmostEqual(result.metrics["cpu_max"], 80.0)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_error_returns_unknown(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = RuntimeError("sensor failed")

        checker = CPUChecker(samples=3, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_checker_name(self, mock_psutil):
        mock_psutil.cpu_percent.side_effect = [25.0]
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker(samples=1, sample_interval=0.0)
        result = checker.check()

        self.assertEqual(result.checker_name, "cpu")
