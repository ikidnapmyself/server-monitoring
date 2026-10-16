"""Tests for the memory checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers._tests.checkers.test_describe_contract import RAW
from apps.checkers.checkers import CheckStatus, MemoryChecker


class MemoryCheckerTests(TestCase):
    """Tests for the MemoryChecker."""

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_ok(self, mock_psutil):
        mock_mem = MagicMock()
        mock_mem.percent = 45.0
        mock_mem.total = 16 * 1024**3  # 16 GB
        mock_mem.used = 7.2 * 1024**3
        mock_mem.available = 8.8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_mem

        checker = MemoryChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["memory_percent"], 45.0)
        self.assertEqual(result.checker_name, "memory")

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_with_swap(self, mock_psutil):
        mock_mem = MagicMock()
        mock_mem.percent = 50.0
        mock_mem.total = 16 * 1024**3
        mock_mem.used = 8 * 1024**3
        mock_mem.available = 8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_mem

        mock_swap = MagicMock()
        mock_swap.percent = 10.0
        mock_swap.total = 8 * 1024**3
        mock_swap.used = 0.8 * 1024**3
        mock_psutil.swap_memory.return_value = mock_swap

        checker = MemoryChecker(include_swap=True)
        result = checker.check()

        self.assertIn("swap_percent", result.metrics)
        self.assertEqual(result.metrics["swap_percent"], 10.0)

    @patch("apps.checkers.checkers.memory.psutil")
    def test_memory_check_generic_exception_returns_error_result(self, mock_psutil):
        """Generic exception in check() produces UNKNOWN via _error_result."""
        mock_psutil.virtual_memory.side_effect = RuntimeError("memory read failed")

        checker = MemoryChecker()
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.UNKNOWN)
        self.assertIn("memory read failed", result.message)


class MemoryDescribeTests(TestCase):
    METRICS = {"memory_percent": 41.0, "memory_available_gb": 9.4, "memory_total_gb": 16.0}

    def test_ok_states_free_of_total(self):
        d = MemoryChecker().describe(CheckStatus.OK, self.METRICS)
        self.assertEqual(d.headline, "Memory at 41%, 9.4 GB free of 16.0 GB")
        self.assertIsNone(d.action)

    def test_warning_says_what_to_do(self):
        d = MemoryChecker().describe(CheckStatus.WARNING, self.METRICS)
        self.assertEqual(d.headline, "Memory at 41%, 9.4 GB free of 16.0 GB")
        self.assertIn("top", d.action.text)

    @patch("apps.checkers.checkers.memory.psutil")
    def test_real_output_reads_in_words(self, mock_psutil):
        mock_mem = MagicMock()
        mock_mem.percent = 95.0
        mock_mem.total = 16 * 1024**3
        mock_mem.used = 15.2 * 1024**3
        mock_mem.available = 0.8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_mem
        result = MemoryChecker().check()
        d = MemoryChecker().describe(result.status, result.metrics, result.message)
        self.assertEqual(d.headline, "Memory at 95%, 819 MB free of 16.0 GB")
        self.assertIsNone(RAW.search(d.headline))
