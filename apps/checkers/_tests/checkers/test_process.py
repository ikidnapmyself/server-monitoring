"""Tests for the process checker."""

from unittest.mock import MagicMock, patch

import psutil
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

    @patch("apps.checkers.checkers.process.psutil.process_iter")
    def test_process_name_partial_match(self, mock_process_iter):
        """Matches when searched name is a substring of process name."""
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 789,
            "name": "python3.11",
            "status": "running",
            "cpu_percent": 2.0,
            "memory_percent": 3.0,
        }
        mock_process_iter.return_value = [mock_proc]

        checker = ProcessChecker(processes=["python"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertTrue(result.metrics["processes"]["python"]["running"])

    @patch("apps.checkers.checkers.process.psutil.process_iter")
    def test_process_iter_nosuchprocess_and_accessdenied(self, mock_process_iter):
        """NoSuchProcess and AccessDenied on individual proc are skipped."""
        bad_proc1 = MagicMock()
        bad_proc1.info = {"name": "zombie"}
        # Accessing .info["name"] is fine, but the next attr raises
        type(bad_proc1).info = property(
            lambda self: (_ for _ in ()).throw(psutil.NoSuchProcess(pid=1))
        )

        bad_proc2 = MagicMock()
        type(bad_proc2).info = property(
            lambda self: (_ for _ in ()).throw(psutil.AccessDenied(pid=2))
        )

        mock_process_iter.return_value = [bad_proc1, bad_proc2]

        checker = ProcessChecker(processes=["nginx"])
        info = checker._is_process_running("nginx")

        self.assertFalse(info["running"])
