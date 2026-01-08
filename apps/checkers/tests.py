"""
Tests for server monitoring checkers.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers import (
    CHECKER_REGISTRY,
    BaseChecker,
    CheckResult,
    CheckStatus,
    CPUChecker,
    DiskChecker,
    MemoryChecker,
    NetworkChecker,
    ProcessChecker,
)


class CheckResultTests(TestCase):
    """Tests for the CheckResult dataclass."""

    def test_is_ok_returns_true_for_ok_status(self):
        result = CheckResult(status=CheckStatus.OK, message="All good")
        self.assertTrue(result.is_ok())

    def test_is_ok_returns_false_for_warning_status(self):
        result = CheckResult(status=CheckStatus.WARNING, message="Warning")
        self.assertFalse(result.is_ok())

    def test_is_critical_returns_true_for_critical_status(self):
        result = CheckResult(status=CheckStatus.CRITICAL, message="Critical")
        self.assertTrue(result.is_critical())

    def test_result_has_all_fields(self):
        result = CheckResult(
            status=CheckStatus.OK,
            message="Test message",
            metrics={"value": 42},
            checker_name="test",
            error=None,
        )
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.message, "Test message")
        self.assertEqual(result.metrics, {"value": 42})
        self.assertEqual(result.checker_name, "test")
        self.assertIsNone(result.error)


class BaseCheckerTests(TestCase):
    """Tests for the BaseChecker ABC."""

    def test_determine_status_ok(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=70, critical_threshold=90)
        self.assertEqual(checker._determine_status(50), CheckStatus.OK)

    def test_determine_status_warning(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=70, critical_threshold=90)
        self.assertEqual(checker._determine_status(75), CheckStatus.WARNING)

    def test_determine_status_critical(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=70, critical_threshold=90)
        self.assertEqual(checker._determine_status(95), CheckStatus.CRITICAL)

    def test_custom_thresholds(self):
        class TestChecker(BaseChecker):
            name = "test"

            def check(self):
                pass

        checker = TestChecker(warning_threshold=50, critical_threshold=80)
        self.assertEqual(checker.warning_threshold, 50)
        self.assertEqual(checker.critical_threshold, 80)


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


class DiskCheckerTests(TestCase):
    """Tests for the DiskChecker."""

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_ok(self, mock_psutil):
        mock_usage = MagicMock()
        mock_usage.percent = 60.0
        mock_usage.total = 500 * 1024**3  # 500 GB
        mock_usage.used = 300 * 1024**3
        mock_usage.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_usage

        checker = DiskChecker(paths=["/"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["worst_percent"], 60.0)

    @patch("apps.checkers.checkers.disk.psutil")
    def test_disk_check_multiple_paths(self, mock_psutil):
        def disk_usage_side_effect(path):
            mock = MagicMock()
            if path == "/":
                mock.percent = 50.0
            elif path == "/data":
                mock.percent = 95.0  # Critical
            mock.total = 500 * 1024**3
            mock.used = mock.percent / 100 * mock.total
            mock.free = mock.total - mock.used
            return mock

        mock_psutil.disk_usage.side_effect = disk_usage_side_effect

        checker = DiskChecker(paths=["/", "/data"])
        result = checker.check()

        self.assertEqual(result.status, CheckStatus.CRITICAL)
        self.assertEqual(result.metrics["worst_path"], "/data")


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


class CheckerRegistryTests(TestCase):
    """Tests for the checker registry."""

    def test_all_checkers_in_registry(self):
        self.assertIn("cpu", CHECKER_REGISTRY)
        self.assertIn("memory", CHECKER_REGISTRY)
        self.assertIn("disk", CHECKER_REGISTRY)
        self.assertIn("network", CHECKER_REGISTRY)
        self.assertIn("process", CHECKER_REGISTRY)

    def test_registry_returns_checker_classes(self):
        self.assertEqual(CHECKER_REGISTRY["cpu"], CPUChecker)
        self.assertEqual(CHECKER_REGISTRY["memory"], MemoryChecker)

