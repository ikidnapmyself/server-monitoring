"""Tests for the memory checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

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
