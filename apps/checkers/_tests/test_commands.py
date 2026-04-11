"""Tests for checker management commands."""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.checkers.checkers.base import CheckResult, CheckStatus


class CheckHealthCommandTests(TestCase):
    """Tests for the check_health management command."""

    REGISTRY_PATH = "apps.checkers.management.commands.check_health.CHECKER_REGISTRY"

    def _make_checker(
        self,
        status=CheckStatus.OK,
        message="All good",
        metrics=None,
        error=None,
        checker_name="cpu",
    ):
        mock_checker = MagicMock()
        mock_checker.__doc__ = "Test checker description"
        mock_checker.return_value.run.return_value = CheckResult(
            status=status,
            message=message,
            metrics=metrics or {},
            checker_name=checker_name,
            error=error,
        )
        return mock_checker

    def test_check_health_calls_run(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.check_health.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command("check_health", "cpu", stdout=StringIO())

        mock_checker.return_value.run.assert_called_once()

    def test_list_flag(self):
        mock_checker = self._make_checker()
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "--list", stdout=out)
        output = out.getvalue()
        self.assertIn("cpu", output)
        self.assertIn("Test checker description", output)
        self.assertIn("Available checkers", output)

    def test_list_flag_no_docstring(self):
        mock_checker = self._make_checker()
        mock_checker.__doc__ = None
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "--list", stdout=out)
        output = out.getvalue()
        self.assertIn("No description", output)

    def test_invalid_checker_name(self):
        with patch.dict(self.REGISTRY_PATH, {"cpu": self._make_checker()}, clear=True):
            with self.assertRaises(CommandError) as ctx:
                call_command("check_health", "nonexistent", stdout=StringIO())
        self.assertIn("Unknown checker(s): nonexistent", str(ctx.exception))

    def test_json_output(self):
        import json as json_mod

        mock_checker = self._make_checker(metrics={"cpu": 10})
        out = StringIO()
        err = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", "--json", stdout=out, stderr=err)
        data = json_mod.loads(out.getvalue())
        self.assertIn("results", data)
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["total"], 1)
        self.assertEqual(data["summary"]["ok"], 1)
        self.assertEqual(data["results"][0]["checker"], "cpu")
        # Status message goes to stderr for json mode
        self.assertIn("Running checkers", err.getvalue())

    def test_warning_status_text_output(self):
        mock_checker = self._make_checker(status=CheckStatus.WARNING, message="High usage")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit"):
                call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("WARNING", output)
        self.assertIn("High usage", output)

    def test_critical_status_text_output(self):
        mock_checker = self._make_checker(status=CheckStatus.CRITICAL, message="Very high")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit"):
                call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("CRITICAL", output)

    def test_unknown_status_text_output(self):
        mock_checker = self._make_checker(status=CheckStatus.UNKNOWN, message="Unknown state")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit"):
                call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("UNKNOWN", output)

    def test_error_message_display(self):
        mock_checker = self._make_checker(status=CheckStatus.CRITICAL, error="Something went wrong")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit"):
                call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Error: Something went wrong", output)

    def test_metrics_display_float(self):
        mock_checker = self._make_checker(metrics={"cpu_percent": 95.5})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("cpu percent: 95.5", output)

    def test_metrics_display_integer(self):
        mock_checker = self._make_checker(metrics={"count": 42})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("count: 42", output)

    def test_unknown_count_in_summary(self):
        mock_checker = self._make_checker(status=CheckStatus.UNKNOWN)
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit"):
                call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Unknown: 1", output)

    def test_critical_summary_styling(self):
        mock_checker = self._make_checker(status=CheckStatus.CRITICAL)
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit"):
                call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Critical: 1", output)

    def test_warning_summary_styling(self):
        mock_checker = self._make_checker(status=CheckStatus.WARNING)
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit"):
                call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Warning: 1", output)

    def test_metrics_space_hogs(self):
        items = [{"path": f"/tmp/file{i}", "size_mb": 100.5, "age_days": 30} for i in range(12)]
        mock_checker = self._make_checker(metrics={"space_hogs": items})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Space Hogs:", output)
        self.assertIn("/tmp/file0", output)
        self.assertIn("100.5 MB", output)
        self.assertIn("30d old", output)
        self.assertIn("... and 2 more", output)

    def test_metrics_old_files(self):
        items = [{"path": "/tmp/old", "size_mb": 50.0}]
        mock_checker = self._make_checker(metrics={"old_files": items})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Old Files:", output)
        self.assertIn("/tmp/old", output)
        self.assertIn("50.0 MB", output)

    def test_metrics_large_files(self):
        items = [{"path": "/tmp/large", "size_mb": 200.0}]
        mock_checker = self._make_checker(metrics={"large_files": items})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Large Files:", output)

    def test_metrics_total_recoverable_mb(self):
        mock_checker = self._make_checker(metrics={"total_recoverable_mb": 500.0})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Total recoverable: 500.0 MB", output)

    def test_metrics_recommendations(self):
        mock_checker = self._make_checker(metrics={"recommendations": ["clean /tmp"]})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Recommendations:", output)
        self.assertIn("- clean /tmp", output)

    def test_metrics_nested_dict(self):
        mock_checker = self._make_checker(
            metrics={
                "paths": {
                    "/": {"total": 100, "used": 50},
                    "free_pct": 50.0,
                    "label": "root",
                }
            }
        )
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("paths:", output)
        self.assertIn("/: total: 100, used: 50", output)
        self.assertIn("free_pct: 50.0", output)
        self.assertIn("label: root", output)

    def test_warning_threshold_and_critical_threshold(self):
        mock_checker = self._make_checker()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command(
                "check_health",
                "cpu",
                "--warning-threshold",
                "80",
                "--critical-threshold",
                "95",
                stdout=StringIO(),
            )
        mock_checker.assert_called_once_with(warning_threshold=80.0, critical_threshold=95.0)

    def test_disk_paths_kwarg(self):
        from pathlib import Path as _Path

        mock_checker = self._make_checker(checker_name="disk")
        with patch.dict(self.REGISTRY_PATH, {"disk": mock_checker}, clear=True):
            call_command(
                "check_health",
                "disk",
                "--disk-paths",
                "/",
                "/var",
                stdout=StringIO(),
            )
        resolved_var = str(_Path("/var").resolve())
        mock_checker.assert_called_once_with(paths=["/", resolved_var])

    def test_ping_hosts_kwarg(self):
        mock_checker = self._make_checker(checker_name="network")
        with patch.dict(self.REGISTRY_PATH, {"network": mock_checker}, clear=True):
            call_command(
                "check_health",
                "network",
                "--ping-hosts",
                "8.8.8.8",
                "1.1.1.1",
                stdout=StringIO(),
            )
        mock_checker.assert_called_once_with(hosts=["8.8.8.8", "1.1.1.1"])

    def test_processes_kwarg(self):
        mock_checker = self._make_checker(checker_name="process")
        with patch.dict(self.REGISTRY_PATH, {"process": mock_checker}, clear=True):
            call_command(
                "check_health",
                "process",
                "--processes",
                "nginx",
                "postgres",
                stdout=StringIO(),
            )
        mock_checker.assert_called_once_with(processes=["nginx", "postgres"])

    def test_fail_on_critical_with_critical(self):
        mock_checker = self._make_checker(status=CheckStatus.CRITICAL)
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit") as mock_exit:
                call_command("check_health", "cpu", "--fail-on-critical", stdout=StringIO())
        mock_exit.assert_called_once_with(1)

    def test_fail_on_critical_with_warning_no_exit(self):
        mock_checker = self._make_checker(status=CheckStatus.WARNING)
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit") as mock_exit:
                call_command("check_health", "cpu", "--fail-on-critical", stdout=StringIO())
        mock_exit.assert_not_called()

    def test_fail_on_warning_with_warning(self):
        mock_checker = self._make_checker(status=CheckStatus.WARNING)
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit") as mock_exit:
                call_command("check_health", "cpu", "--fail-on-warning", stdout=StringIO())
        mock_exit.assert_called_once_with(1)

    def test_fail_on_warning_with_ok_no_exit(self):
        mock_checker = self._make_checker(status=CheckStatus.OK)
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit") as mock_exit:
                call_command("check_health", "cpu", "--fail-on-warning", stdout=StringIO())
        mock_exit.assert_not_called()

    def test_exit_code_2_for_critical_default(self):
        mock_checker = self._make_checker(status=CheckStatus.CRITICAL)
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit") as mock_exit:
                call_command("check_health", "cpu", stdout=StringIO())
        mock_exit.assert_called_once_with(2)

    def test_exit_code_1_for_unknown_default(self):
        mock_checker = self._make_checker(status=CheckStatus.UNKNOWN)
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit") as mock_exit:
                call_command("check_health", "cpu", stdout=StringIO())
        mock_exit.assert_called_once_with(1)

    def test_no_exit_for_ok(self):
        mock_checker = self._make_checker(status=CheckStatus.OK)
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with patch("sys.exit") as mock_exit:
                call_command("check_health", "cpu", stdout=StringIO())
        mock_exit.assert_not_called()

    def test_run_all_checkers_when_none_specified(self):
        cpu_checker = self._make_checker(checker_name="cpu")
        mem_checker = self._make_checker(checker_name="memory")
        out = StringIO()
        with patch.dict(
            self.REGISTRY_PATH, {"cpu": cpu_checker, "memory": mem_checker}, clear=True
        ):
            call_command("check_health", stdout=out)
        cpu_checker.return_value.run.assert_called_once()
        mem_checker.return_value.run.assert_called_once()

    def test_json_output_with_multiple_statuses(self):
        import json as json_mod

        ok_checker = self._make_checker(status=CheckStatus.OK, checker_name="cpu")
        warn_checker = self._make_checker(status=CheckStatus.WARNING, checker_name="memory")
        crit_checker = self._make_checker(status=CheckStatus.CRITICAL, checker_name="disk")
        unknown_checker = self._make_checker(status=CheckStatus.UNKNOWN, checker_name="net")
        out = StringIO()
        err = StringIO()
        with patch.dict(
            self.REGISTRY_PATH,
            {
                "cpu": ok_checker,
                "memory": warn_checker,
                "disk": crit_checker,
                "net": unknown_checker,
            },
            clear=True,
        ):
            with patch("sys.exit"):
                call_command("check_health", "--json", stdout=out, stderr=err)
        data = json_mod.loads(out.getvalue())
        self.assertEqual(data["summary"]["ok"], 1)
        self.assertEqual(data["summary"]["warning"], 1)
        self.assertEqual(data["summary"]["critical"], 1)
        self.assertEqual(data["summary"]["unknown"], 1)

    def test_text_output_no_metrics(self):
        """CheckResult with empty metrics dict should not output metric lines."""
        mock_checker = self._make_checker(metrics={})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("OK", output)

    def test_metrics_platform_skipped(self):
        """The 'platform' key should be skipped in metrics output."""
        mock_checker = self._make_checker(metrics={"platform": "linux", "usage": 42})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertNotIn("platform", output)
        self.assertIn("usage: 42", output)


class RunCheckCommandTests(TestCase):
    """Tests for the run_check management command."""

    def test_run_check_calls_run(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.run_check.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command("run_check", "cpu", stdout=StringIO())

        mock_checker.return_value.run.assert_called_once()

    def test_run_check_passes_samples_to_cpu(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu_percent": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.run_check.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command(
                "run_check",
                "cpu",
                "--samples",
                "3",
                "--sample-interval",
                "0.5",
                stdout=StringIO(),
            )

        mock_checker.assert_called_once_with(samples=3, sample_interval=0.5)

    def test_run_check_cpu_default_no_extra_kwargs(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu_percent": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.run_check.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command("run_check", "cpu", stdout=StringIO())

        mock_checker.assert_called_once_with()

    REGISTRY_PATH = "apps.checkers.management.commands.run_check.CHECKER_REGISTRY"

    def _make_checker(
        self,
        status=CheckStatus.OK,
        message="All good",
        metrics=None,
        error=None,
        checker_name="cpu",
    ):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=status,
            message=message,
            metrics=metrics if metrics is not None else {"cpu": 10},
            checker_name=checker_name,
            error=error,
        )
        return mock_checker

    def test_unknown_checker_raises_error(self):
        with patch.dict(self.REGISTRY_PATH, {"cpu": self._make_checker()}, clear=True):
            with self.assertRaises(CommandError) as ctx:
                call_command("run_check", "bogus", stdout=StringIO())
        self.assertIn("Unknown checker: bogus", str(ctx.exception))

    def test_warning_threshold(self):
        mock_checker = self._make_checker()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", "--warning-threshold", "80", stdout=StringIO())
        mock_checker.assert_called_once_with(warning_threshold=80.0)

    def test_critical_threshold(self):
        mock_checker = self._make_checker()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", "--critical-threshold", "95", stdout=StringIO())
        mock_checker.assert_called_once_with(critical_threshold=95.0)

    def test_per_cpu_flag(self):
        mock_checker = self._make_checker()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", "--per-cpu", stdout=StringIO())
        mock_checker.assert_called_once_with(per_cpu=True)

    def test_memory_include_swap(self):
        mock_checker = self._make_checker(checker_name="memory")
        with patch.dict(self.REGISTRY_PATH, {"memory": mock_checker}, clear=True):
            call_command("run_check", "memory", "--include-swap", stdout=StringIO())
        mock_checker.assert_called_once_with(include_swap=True)

    def test_disk_paths(self):
        from pathlib import Path as _Path

        mock_checker = self._make_checker(checker_name="disk")
        with patch.dict(self.REGISTRY_PATH, {"disk": mock_checker}, clear=True):
            call_command("run_check", "disk", "--paths", "/", "/tmp", stdout=StringIO())
        resolved_tmp = str(_Path("/tmp").resolve())
        mock_checker.assert_called_once_with(paths=["/", resolved_tmp])

    def test_network_hosts(self):
        mock_checker = self._make_checker(checker_name="network")
        with patch.dict(self.REGISTRY_PATH, {"network": mock_checker}, clear=True):
            call_command("run_check", "network", "--hosts", "8.8.8.8", stdout=StringIO())
        mock_checker.assert_called_once_with(hosts=["8.8.8.8"])

    def test_process_names(self):
        mock_checker = self._make_checker(checker_name="process")
        with patch.dict(self.REGISTRY_PATH, {"process": mock_checker}, clear=True):
            call_command("run_check", "process", "--names", "nginx", stdout=StringIO())
        mock_checker.assert_called_once_with(processes=["nginx"])

    def test_json_output(self):
        import json as json_mod

        mock_checker = self._make_checker(metrics={"cpu": 10})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", "--json", stdout=out)
        data = json_mod.loads(out.getvalue())
        self.assertEqual(data["checker"], "cpu")
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["metrics"]["cpu"], 10)

    def test_warning_status_output(self):
        mock_checker = self._make_checker(status=CheckStatus.WARNING, message="High usage")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("WARNING", output)

    def test_critical_status_output(self):
        mock_checker = self._make_checker(status=CheckStatus.CRITICAL, message="Very high")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("CRITICAL", output)

    def test_unknown_status_output(self):
        mock_checker = self._make_checker(status=CheckStatus.UNKNOWN, message="Unknown state")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("UNKNOWN", output)

    def test_error_display(self):
        mock_checker = self._make_checker(status=CheckStatus.CRITICAL, error="something went wrong")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Error: something went wrong", output)

    def test_no_metrics(self):
        mock_checker = self._make_checker(metrics={})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertNotIn("Metrics:", output)

    def test_memory_no_swap(self):
        mock_checker = self._make_checker(checker_name="memory")
        with patch.dict(self.REGISTRY_PATH, {"memory": mock_checker}, clear=True):
            call_command("run_check", "memory", stdout=StringIO())
        mock_checker.assert_called_once_with()

    def test_disk_no_paths(self):
        mock_checker = self._make_checker(checker_name="disk")
        with patch.dict(self.REGISTRY_PATH, {"disk": mock_checker}, clear=True):
            call_command("run_check", "disk", stdout=StringIO())
        mock_checker.assert_called_once_with()

    def test_network_no_hosts(self):
        mock_checker = self._make_checker(checker_name="network")
        with patch.dict(self.REGISTRY_PATH, {"network": mock_checker}, clear=True):
            call_command("run_check", "network", stdout=StringIO())
        mock_checker.assert_called_once_with()

    def test_process_no_names(self):
        mock_checker = self._make_checker(checker_name="process")
        with patch.dict(self.REGISTRY_PATH, {"process": mock_checker}, clear=True):
            call_command("run_check", "process", stdout=StringIO())
        mock_checker.assert_called_once_with()

    def test_other_checker_no_specific_options(self):
        """A checker not in cpu/memory/disk/network/process skips all specific branches."""
        mock_checker = self._make_checker(checker_name="custom")
        with patch.dict(self.REGISTRY_PATH, {"custom": mock_checker}, clear=True):
            call_command("run_check", "custom", stdout=StringIO())
        mock_checker.assert_called_once_with()

    def test_nested_dict_metrics(self):
        mock_checker = self._make_checker(metrics={"hosts": {"8.8.8.8": {"latency": 5}}})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("hosts:", output)
        self.assertIn("8.8.8.8: {'latency': 5}", output)

    # Preflight command tests moved to apps/checkers/_tests/preflight/test_command.py
