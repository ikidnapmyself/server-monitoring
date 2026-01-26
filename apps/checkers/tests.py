"""
Tests for server monitoring checkers.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.test.utils import override_settings

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
    get_enabled_checkers,
    is_checker_enabled,
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


class SystemChecksTests(TestCase):
    """Tests for Django system checks (database, migrations, crontab)."""

    def test_database_check_success(self):
        """Test that database check passes when database is accessible."""
        from apps.checkers.checks import check_database_connection

        errors = check_database_connection(app_configs=None)
        # Should return empty list (no errors) if database is configured
        self.assertEqual(errors, [])

    def test_database_check_failure(self):
        """Test that database check fails with invalid database config."""
        from unittest.mock import patch

        from apps.checkers.checks import check_database_connection

        with patch("django.db.connections") as mock_connections:
            mock_conn = MagicMock()
            mock_conn.ensure_connection.side_effect = Exception("Connection refused")
            mock_connections.__iter__ = lambda self: iter(["default"])
            mock_connections.__getitem__ = lambda self, key: mock_conn

            errors = check_database_connection(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.E001")

    def test_migrations_check_no_pending(self):
        """Test that migrations check passes when all migrations are applied."""
        from unittest.mock import MagicMock, patch

        from apps.checkers.checks import check_pending_migrations

        with patch("django.db.connections") as mock_connections:
            mock_conn = MagicMock()
            mock_connections.__iter__ = lambda self: iter(["default"])
            mock_connections.__getitem__ = lambda self, key: mock_conn

            with patch("django.db.migrations.executor.MigrationExecutor") as mock_executor_class:
                mock_executor = MagicMock()
                mock_executor.migration_plan.return_value = []  # No pending migrations
                mock_executor_class.return_value = mock_executor

                errors = check_pending_migrations(app_configs=None)
                self.assertEqual(errors, [])

    def test_migrations_check_pending(self):
        """Test that migrations check warns when there are pending migrations."""
        from unittest.mock import MagicMock, patch

        from apps.checkers.checks import check_pending_migrations

        with patch("django.db.connections") as mock_connections:
            mock_conn = MagicMock()
            mock_connections.__iter__ = lambda self: iter(["default"])
            mock_connections.__getitem__ = lambda self, key: mock_conn

            with patch("django.db.migrations.executor.MigrationExecutor") as mock_executor_class:
                # Create mock migration
                mock_migration = MagicMock()
                mock_migration.app_label = "myapp"
                mock_migration.name = "0001_initial"

                mock_executor = MagicMock()
                mock_executor.migration_plan.return_value = [(mock_migration, False)]
                mock_executor_class.return_value = mock_executor

                errors = check_pending_migrations(app_configs=None)
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0].id, "checkers.W001")
                self.assertIn("pending migration", errors[0].msg.lower())

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_with_cron_configured(self, mock_is_testing):
        """Test that crontab check passes when cron job is configured."""
        from unittest.mock import patch

        from apps.checkers.checks import check_crontab_configuration

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "*/5 * * * * cd /path && uv run python manage.py check_and_alert --json # server-maintanence health check"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_no_cron_job(self, mock_is_testing):
        """Test that crontab check warns when cron job is missing."""
        from unittest.mock import patch

        from apps.checkers.checks import check_crontab_configuration

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "0 0 * * * /some/other/job"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W004")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_no_crontab(self, mock_is_testing):
        """Test that crontab check warns when no crontab exists."""
        from unittest.mock import patch

        from apps.checkers.checks import check_crontab_configuration

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "no crontab for user"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W002")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_command_not_found(self, mock_is_testing):
        """Test that crontab check handles missing crontab command."""
        from unittest.mock import patch

        from apps.checkers.checks import check_crontab_configuration

        with patch("subprocess.run", side_effect=FileNotFoundError()):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W006")


class CheckerEnablementTests(TestCase):
    """Tests for checker enable/disable settings."""

    @override_settings(CHECKERS_SKIP_ALL=True, CHECKERS_SKIP=[])
    def test_skip_all_disables_every_checker(self):
        for name in CHECKER_REGISTRY.keys():
            self.assertFalse(is_checker_enabled(name))

        self.assertEqual(get_enabled_checkers(), {})

    @override_settings(CHECKERS_SKIP_ALL=False, CHECKERS_SKIP=["network", "process"])
    def test_skip_list_disables_only_selected_checkers(self):
        self.assertFalse(is_checker_enabled("network"))
        self.assertFalse(is_checker_enabled("process"))
        self.assertTrue(is_checker_enabled("cpu"))
        self.assertTrue(is_checker_enabled("memory"))

        enabled = get_enabled_checkers()
        self.assertIn("cpu", enabled)
        self.assertIn("memory", enabled)
        self.assertNotIn("network", enabled)
        self.assertNotIn("process", enabled)
