"""Tests for the network checker."""

from subprocess import TimeoutExpired
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


class ParseLatencyTests(TestCase):
    """Tests for NetworkChecker._parse_latency across platforms."""

    def test_parse_latency_macos_format(self):
        """macOS: round-trip min/avg/max/stddev = 1.234/5.678/9.012/1.234 ms"""
        checker = NetworkChecker()
        output = (
            "PING 8.8.8.8 (8.8.8.8): 56 data bytes\n"
            "round-trip min/avg/max/stddev = 1.234/5.678/9.012/1.234 ms\n"
        )
        latency = checker._parse_latency(output)
        self.assertAlmostEqual(latency, 5.678, places=3)

    def test_parse_latency_linux_format(self):
        """Linux: rtt min/avg/max/mdev = 1.234/5.678/9.012/1.234 ms"""
        checker = NetworkChecker()
        output = (
            "PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n"
            "rtt min/avg/max/mdev = 1.234/5.678/9.012/1.234 ms\n"
        )
        latency = checker._parse_latency(output)
        self.assertAlmostEqual(latency, 5.678, places=3)

    def test_parse_latency_windows_format(self):
        """Windows: Average = 5ms"""
        checker = NetworkChecker()
        output = (
            "Ping statistics for 8.8.8.8:\n" "    Minimum = 1ms, Maximum = 9ms, Average = 5ms\n"
        )
        latency = checker._parse_latency(output)
        self.assertAlmostEqual(latency, 5.0, places=1)

    def test_parse_latency_unparseable_returns_none(self):
        """Unparseable output returns None."""
        checker = NetworkChecker()
        latency = checker._parse_latency("no latency info here")
        self.assertIsNone(latency)


class PingHostEdgeCaseTests(TestCase):
    """Tests for NetworkChecker._ping_host edge cases."""

    @patch("apps.checkers.checkers.network.subprocess.run")
    def test_ping_host_timeout_expired(self, mock_run):
        """TimeoutExpired returns (False, None)."""
        mock_run.side_effect = TimeoutExpired(cmd=["ping"], timeout=5)  # nosemgrep

        checker = NetworkChecker()
        success, latency = checker._ping_host("8.8.8.8")

        self.assertFalse(success)
        self.assertIsNone(latency)

    @patch("apps.checkers.checkers.network.subprocess.run")
    def test_ping_host_generic_exception(self, mock_run):
        """Generic exception returns (False, None)."""
        mock_run.side_effect = OSError("network down")

        checker = NetworkChecker()
        success, latency = checker._ping_host("8.8.8.8")

        self.assertFalse(success)
        self.assertIsNone(latency)

    @patch("apps.checkers.checkers.network.subprocess.run")
    def test_subprocess_timeout_scales_with_ping_count(self, mock_run):
        """Subprocess timeout is ping_count * per-host timeout + 1."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        checker = NetworkChecker(ping_count=3, timeout=5.0)
        checker._ping_host("8.8.8.8")

        _, kwargs = mock_run.call_args
        self.assertAlmostEqual(kwargs["timeout"], 3 * 5.0 + 1)
