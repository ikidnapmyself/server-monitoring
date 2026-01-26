"""Tests for the network checker."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers import CheckStatus, NetworkChecker


class NetworkCheckerTests(TestCase):
    """Tests for the NetworkChecker."""

    @patch("apps.checkers.checkers.network.subprocess.run")
    def test_network_check_all_reachable(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="avg = 10ms")

        checker = NetworkChecker(hosts=["8.8.8.8", "1.1.1.1"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["reachable_count"], 2)

    @patch("apps.checkers.checkers.network.subprocess.run")
    def test_network_check_partial_failure(self, mock_run):
        def run_side_effect(cmd, **kwargs):
            host = cmd[-1]
            if host == "8.8.8.8":
                return MagicMock(returncode=0, stdout="avg = 10ms")
            else:
                return MagicMock(returncode=1, stdout="")

        mock_run.side_effect = run_side_effect

        checker = NetworkChecker(hosts=["8.8.8.8", "unreachable.invalid"])
        result = checker.check()

        # 50% reachable = WARNING (between 50% critical and 70% warning)
        self.assertEqual(result.metrics["reachable_count"], 1)
