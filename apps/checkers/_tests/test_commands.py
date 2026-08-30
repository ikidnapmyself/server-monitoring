"""Tests for checker management commands."""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import TestCase, TransactionTestCase, override_settings

from apps.alerts.models import Alert, Incident, Node
from apps.checkers.checkers.base import CheckResult, CheckStatus
from apps.notify.models import NotificationChannel
from apps.orchestration.models import (
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)


class CheckHealthCommandTests(TestCase):
    """Tests for the check_health management command."""

    REGISTRY_PATH = "apps.checkers.management.commands.check_health.CHECKER_REGISTRY"

    def setUp(self):
        """Keep the analysis these runs now trigger off the real filesystem.

        The command orchestrates what it records, so a CRITICAL "disk" checker
        here reaches LocalProvider, which walks all of ``/`` looking for large
        files — minutes per test, and different on every machine. These tests are
        about output and exit codes; the pipeline itself still runs for real, only
        the scan is stubbed. What the analysis produces is asserted in
        ``CheckHealthAlertTests``.
        """
        patcher = patch(
            "apps.intelligence.providers.local.LocalRecommendationProvider.analyze",
            return_value=[],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_check_health_uses_metrics_formatter(self):
        """Smoke test: command pipes metrics through write_metrics."""
        items = [{"path": f"/tmp/file{i}", "size_mb": 10.0} for i in range(5)]
        mock_checker = self._make_checker(metrics={"space_hogs": items})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("Space Hogs: 50.0 MB (5 items, all shown)", output)

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

    def test_disk_paths_rejected_outside_allowed_roots(self):
        """PathNotAllowedError is caught and re-raised as CommandError."""
        mock_checker = self._make_checker(checker_name="disk")
        with patch.dict(self.REGISTRY_PATH, {"disk": mock_checker}, clear=True):
            with self.assertRaises(CommandError):
                call_command(
                    "check_health", "disk", "--disk-paths", "/root/.ssh", stdout=StringIO()
                )

    def test_metrics_platform_skipped(self):
        """The 'platform' key should be skipped in metrics output."""
        mock_checker = self._make_checker(metrics={"platform": "linux", "usage": 42})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("check_health", "cpu", stdout=out)
        output = out.getvalue()
        self.assertNotIn("platform", output)
        self.assertIn("usage: 42", output)


@override_settings(INSTANCE_ID="solo-mac")
class CheckHealthAlertTests(TestCase):
    """check_health records alerts for this machine, inline and inbox-free."""

    REGISTRY_PATH = CheckHealthCommandTests.REGISTRY_PATH
    # Same mock-checker helper the command tests above use.
    _make_checker = CheckHealthCommandTests._make_checker

    def _run_firing_cpu(self, *args):
        """Run check_health over one CRITICAL cpu checker; returns (stdout, stderr).

        A critical result exits 2, so the SystemExit is expected, not a failure.
        """
        out, err = StringIO(), StringIO()
        mock_checker = self._make_checker(
            status=CheckStatus.CRITICAL,
            message="CPU at 99%",
            checker_name="cpu",
        )
        # The command enqueues inside a transaction and lets ``on_commit`` fire the
        # drain, so the drain never claims runs the transaction has not committed.
        # ``TestCase`` wraps each test in a transaction that never commits, so
        # without this the drain the command really performs would not happen here.
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(SystemExit) as ctx:
                    call_command("check_health", "cpu", *args, stdout=out, stderr=err)
        self.assertEqual(ctx.exception.code, 2)
        return out.getvalue(), err.getvalue()

    def test_writes_an_alert_for_a_firing_checker(self):
        self._run_firing_cpu()
        self.assertTrue(Alert.objects.filter(fingerprint="check:solo-mac:cpu").exists())

    def test_no_alert_flag_writes_nothing(self):
        self._run_firing_cpu("--no-alert")
        self.assertFalse(Alert.objects.exists())

    def test_incidents_are_analyzed_in_the_same_call(self):
        """The single-machine install has nobody draining an inbox.

        So the command drains its own runs before returning: nothing may be left
        PENDING, and the analysis the operator ran the command for has happened by
        the time they get their prompt back.
        """
        self._run_firing_cpu()
        self.assertTrue(PipelineRun.objects.exists())
        self.assertFalse(PipelineRun.objects.filter(status=PipelineStatus.PENDING).exists())
        self.assertTrue(
            StageExecution.objects.filter(
                stage=PipelineStage.ANALYZE, status=StageStatus.SUCCEEDED
            ).exists()
        )

    def test_alerts_and_incidents_are_still_written(self):
        """Orchestrating is added to the recording, not swapped for it."""
        self._run_firing_cpu()
        alert = Alert.objects.get(fingerprint="check:solo-mac:cpu")
        self.assertIsNotNone(alert.incident)
        self.assertEqual(Incident.objects.count(), 1)

    def test_no_alert_still_enqueues_nothing(self):
        """--no-alert is print-only: nothing written, so nothing to orchestrate."""
        self._run_firing_cpu("--no-alert")
        self.assertFalse(Alert.objects.exists())
        self.assertFalse(PipelineRun.objects.exists())

    def test_no_notify_reaches_the_enqueued_runs(self):
        """SSH in, read the analysis, page nobody."""
        self._run_firing_cpu("--no-notify")
        run = PipelineRun.objects.get()
        self.assertTrue(run.inbound_payload.get("no_notify"))
        self.assertFalse(StageExecution.objects.filter(stage=PipelineStage.NOTIFY).exists())

    def test_nothing_is_delivered_without_an_active_channel(self):
        """The laptop case: the analysis happens, nobody is paged."""
        self.assertFalse(NotificationChannel.objects.filter(is_active=True).exists())
        self._run_firing_cpu()
        self.assertTrue(
            StageExecution.objects.filter(
                stage=PipelineStage.ANALYZE, status=StageStatus.SUCCEEDED
            ).exists()
        )
        self.assertFalse(
            StageExecution.objects.filter(
                stage=PipelineStage.NOTIFY, status=StageStatus.SUCCEEDED
            ).exists()
        )

    def test_registers_this_machine_as_a_node(self):
        self._run_firing_cpu()
        self.assertTrue(Node.objects.filter(instance_id="solo-mac").exists())

    # The bridge swallows its own failures: _process_parsed_payload catches every
    # exception and folds the message into the returned CheckAlertResult.errors.
    # So these patch a seam INSIDE the bridge (the alert write itself) rather than
    # the bridge call, which production never makes raise.
    ALERT_WRITE_PATH = "apps.alerts.services.AlertOrchestrator._process_alert"

    def test_alert_write_failure_does_not_change_exit_code_or_output(self):
        """An unmigrated or missing database must not break a health check."""
        with patch(self.ALERT_WRITE_PATH, side_effect=OperationalError("no such table")):
            stdout, _ = self._run_firing_cpu()
        self.assertIn("CPU at 99%", stdout)
        self.assertFalse(Alert.objects.exists())

    def test_alert_write_failure_is_logged(self):
        """The stderr line alone would leave a real bridge bug undiagnosable."""
        with patch(self.ALERT_WRITE_PATH, side_effect=OperationalError("no such table")):
            with self.assertLogs(
                "apps.checkers.management.commands.check_health", level="ERROR"
            ) as logs:
                self._run_firing_cpu()
        self.assertIn("Alert recording failed", logs.output[0])
        self.assertIn("no such table", logs.output[0])

    def test_alert_write_failure_reports_on_stderr_not_stdout(self):
        """So --json output on stdout stays parseable."""
        with patch(self.ALERT_WRITE_PATH, side_effect=OperationalError("no such table")):
            stdout, stderr = self._run_firing_cpu()
        self.assertIn("Alert recording skipped", stderr)
        self.assertIn("no such table", stderr)
        self.assertNotIn("Alert recording skipped", stdout)

    def test_a_crash_at_the_enqueue_leaves_no_orphaned_incident(self):
        """The enqueue commits with the writes that justify it, or not at all.

        An incident written with no run never self-heals: the next tick reports the
        same condition at the same severity, so nothing is material and nothing is
        enqueued, and ``reclaim_stuck`` only rescues rows that are already runs.
        """
        with patch(
            "apps.orchestration.intake.enqueue_for",
            side_effect=OperationalError("killed mid-enqueue"),
        ):
            _, stderr = self._run_firing_cpu()

        self.assertIn("killed mid-enqueue", stderr)
        self.assertFalse(Alert.objects.exists())
        self.assertFalse(Incident.objects.exists())
        self.assertFalse(PipelineRun.objects.exists())

    def test_a_raised_bridge_failure_is_still_reported(self):
        """The try/except still guards the import and the constructor."""
        with patch(
            "apps.alerts.check_integration.CheckAlertBridge.process_check_results",
            side_effect=OperationalError("bridge exploded"),
        ):
            _, stderr = self._run_firing_cpu()
        self.assertIn("Alert recording skipped", stderr)
        self.assertIn("bridge exploded", stderr)


@override_settings(INSTANCE_ID="solo-mac")
class CheckHealthDrainFailureTests(TransactionTestCase):
    """A broken drain must not break the health check.

    The drain runs on the commit of the command's own transaction, so it is inside
    the command's ``try`` and the command reports it. Proving that needs a real
    commit, which is why this is a ``TransactionTestCase``: under ``TestCase`` the
    enclosing transaction never commits, and the on-commit drain would run in the
    test rather than in the command.

    Nothing here needs a routing lane — ``execute_run`` is stubbed to fail — so the
    flush this class ends with costs the tests that follow nothing.
    """

    REGISTRY_PATH = CheckHealthCommandTests.REGISTRY_PATH
    _make_checker = CheckHealthCommandTests._make_checker

    def _run_firing_cpu(self):
        out, err = StringIO(), StringIO()
        mock_checker = self._make_checker(
            status=CheckStatus.CRITICAL,
            message="CPU at 99%",
            checker_name="cpu",
        )
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                call_command("check_health", "cpu", stdout=out, stderr=err)
        self.assertEqual(ctx.exception.code, 2)
        return out.getvalue(), err.getvalue()

    def test_an_orchestration_failure_does_not_change_exit_code_or_stdout(self):
        """A health check must still print its results if the pipeline breaks."""
        with patch(
            "apps.orchestration.orchestrator.PipelineOrchestrator.execute_run",
            side_effect=OperationalError("orchestrator exploded"),
        ):
            stdout, stderr = self._run_firing_cpu()
        self.assertIn("CPU at 99%", stdout)
        self.assertIn("orchestrator exploded", stderr)
        self.assertNotIn("orchestrator exploded", stdout)
        # The alerts were still recorded; only the drain failed. The drain runs
        # after the commit, so its failure cannot take the writes back.
        self.assertTrue(Alert.objects.exists())

    def test_an_orchestration_failure_is_logged(self):
        """The stderr line alone would leave a real pipeline bug undiagnosable."""
        with patch(
            "apps.orchestration.orchestrator.PipelineOrchestrator.execute_run",
            side_effect=OperationalError("orchestrator exploded"),
        ):
            with self.assertLogs(
                "apps.checkers.management.commands.check_health", level="ERROR"
            ) as logs:
                self._run_firing_cpu()
        self.assertIn("orchestrator exploded", "\n".join(logs.output))


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

    def test_skipped_check_hides_metrics(self):
        mock_checker = self._make_checker(
            status=CheckStatus.OK,
            message="Skipped: not Linux",
            metrics={"platform": "darwin", "reboot_required": False},
        )
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"reboot_debian": mock_checker}, clear=True):
            call_command("run_check", "reboot_debian", stdout=out)
        output = out.getvalue()
        self.assertIn("Skipped: not Linux", output)
        self.assertNotIn("Metrics:", output)
        self.assertNotIn("platform: darwin", output)

    def test_skipped_check_keeps_metrics_in_json(self):
        import json as json_mod

        mock_checker = self._make_checker(
            status=CheckStatus.OK,
            message="Skipped: not Linux",
            metrics={"platform": "darwin", "reboot_required": False},
        )
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"reboot_debian": mock_checker}, clear=True):
            call_command("run_check", "reboot_debian", "--json", stdout=out)
        data = json_mod.loads(out.getvalue())
        self.assertEqual(data["metrics"]["platform"], "darwin")
        self.assertEqual(data["metrics"]["reboot_required"], False)

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
        self.assertIn("8.8.8.8: latency: 5", output)

    def test_disk_paths_rejected_outside_allowed_roots(self):
        """PathNotAllowedError is caught and re-raised as CommandError."""
        mock_checker = self._make_checker(checker_name="disk")
        with patch.dict(self.REGISTRY_PATH, {"disk": mock_checker}, clear=True):
            with self.assertRaises(CommandError):
                call_command("run_check", "disk", "--paths", "/root/.ssh", stdout=StringIO())

    def test_run_check_wraps_metrics_with_label(self):
        """The metrics block is preceded by a 'Metrics:' header line."""
        mock_checker = self._make_checker(metrics={"cpu_percent": 15.5})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("  Metrics:", output)

    def test_run_check_disk_metrics_use_section_format(self):
        """Disk space_hogs render through the shared helper, not as repr()."""
        items = [{"path": f"/tmp/file{i}", "size_mb": 100.5, "age_days": 30} for i in range(12)]
        mock_checker = self._make_checker(metrics={"space_hogs": items}, checker_name="disk_common")
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"disk_common": mock_checker}, clear=True):
            call_command("run_check", "disk_common", stdout=out)
        output = out.getvalue()
        self.assertIn("Space Hogs: 1206.0 MB (12 items, top 10 shown)", output)
        self.assertIn("... and 2 more  (201.0 MB)", output)
        self.assertNotIn("[{", output)

    def test_run_check_flat_metric_uses_helper_format(self):
        """Flat keys render with underscore-to-space and float :.1f formatting."""
        mock_checker = self._make_checker(metrics={"cpu_percent": 15.5})
        out = StringIO()
        with patch.dict(self.REGISTRY_PATH, {"cpu": mock_checker}, clear=True):
            call_command("run_check", "cpu", stdout=out)
        output = out.getvalue()
        self.assertIn("cpu percent: 15.5", output)
        self.assertNotIn("cpu_percent:", output)

    # Preflight command tests moved to apps/checkers/_tests/preflight/test_command.py
