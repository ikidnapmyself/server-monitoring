"""Tests for the CPU checker."""

from unittest.mock import patch

from django.test import TestCase

from apps.checkers.checkers import CheckStatus, CPUChecker


class CPUCheckerTests(TestCase):
    """Tests for the CPUChecker."""

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_ok(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 25.0
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["cpu_percent"], 25.0)
        self.assertEqual(result.checker_name, "cpu")

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_critical(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = 95.0
        mock_psutil.cpu_count.return_value = 4

        checker = CPUChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)

    @patch("apps.checkers.checkers.cpu.psutil")
    def test_cpu_check_per_cpu(self, mock_psutil):
        mock_psutil.cpu_percent.return_value = [20.0, 30.0, 80.0, 25.0]

        checker = CPUChecker(per_cpu=True)
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.WARNING)  # 80% max
        self.assertEqual(result.metrics["cpu_percent"], 80.0)
        self.assertEqual(result.metrics["per_cpu_percent"], [20.0, 30.0, 80.0, 25.0])
