"""Tests for the process checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers import CheckStatus, ProcessChecker


class ProcessCheckerTests(TestCase):
    """Tests for the ProcessChecker."""

    def test_process_check_no_processes_configured(self):
        checker = ProcessChecker(processes=[])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["total_count"], 0)

    @patch("apps.checkers.checkers.process.psutil.process_iter")
    def test_process_check_all_running(self, mock_process_iter):
        mock_proc1 = MagicMock()
        mock_proc1.info = {
            "pid": 123,
            "name": "nginx",
            "status": "running",
            "cpu_percent": 1.0,
            "memory_percent": 2.0,
        }
        mock_proc2 = MagicMock()
        mock_proc2.info = {
            "pid": 456,
            "name": "postgres",
            "status": "running",
            "cpu_percent": 5.0,
            "memory_percent": 10.0,
        }
        mock_process_iter.return_value = [mock_proc1, mock_proc2]

        checker = ProcessChecker(processes=["nginx", "postgres"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["running_count"], 2)

    @patch("apps.checkers.checkers.process.psutil.process_iter")
    def test_process_check_missing_process(self, mock_process_iter):
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 123,
            "name": "nginx",
            "status": "running",
            "cpu_percent": 1.0,
            "memory_percent": 2.0,
        }
        mock_process_iter.return_value = [mock_proc]

        checker = ProcessChecker(processes=["nginx", "missing_service"])
        result = checker.check()

        # Only 50% running = CRITICAL (at threshold)
        self.assertEqual(result.metrics["running_count"], 1)
        self.assertIn("missing_service", result.message)
