"""Tests for the ``run_pipeline`` management command.

``run_pipeline`` is a producer like every other: it writes alerts, lets incidents
form, and hands the materially changed ones to ``apps.orchestration.intake``. It
differs from the webhook only in draining its own runs, because a CLI caller
expects one call to finish. There is no entry stage and no driver payload
wrapper any more, so these tests assert the rows that actually appear rather
than the shape of a dict handed to the orchestrator.
"""

import io
import json
import os
import tempfile
from io import StringIO
from unittest import mock
from unittest.mock import MagicMock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.alerts.models import Alert, Incident
from apps.checkers.checkers.base import CheckResult, CheckStatus
from apps.orchestration.models import (
    PipelineOrigin,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)

REGISTRY_PATH = "apps.alerts.check_integration.CHECKER_REGISTRY"


def make_checker(
    status=CheckStatus.CRITICAL,
    message="CPU at 99%",
    metrics=None,
    checker_name="cpu",
):
    """A checker class whose instances return one fixed result."""
    mock_checker = MagicMock()
    mock_checker.__doc__ = "Test checker description"
    mock_checker.return_value.run.return_value = CheckResult(
        status=status,
        message=message,
        metrics=metrics or {},
        checker_name=checker_name,
    )
    return mock_checker


class StubAnalysisMixin:
    """Keep the analysis these runs trigger off the real filesystem.

    The command now drains its own runs, so a CRITICAL result reaches
    LocalProvider, which walks all of ``/`` looking for large files — minutes per
    test. The pipeline itself still runs for real; only the scan is stubbed.
    """

    def setUp(self):
        super().setUp()
        patcher = patch(
            "apps.intelligence.providers.local.LocalRecommendationProvider.analyze",
            return_value=[],
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class RunPipelineReplayTests(StubAnalysisMixin, TestCase):
    """--sample / --payload / --file replay a webhook-shaped payload, inline."""

    def setUp(self):
        super().setUp()
        # The drained lane runs CHECK against this machine for real, which takes
        # tens of seconds and writes alerts of its own. One healthy stub checker
        # keeps these tests about the ingest: an OK first sighting records nothing.
        patcher = patch.dict(
            REGISTRY_PATH,
            {"cpu": make_checker(status=CheckStatus.OK, message="All good")},
            clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sample_payload_creates_alerts_and_drains(self):
        """The CLI caller expects one call to finish: nothing left PENDING.

        The command enqueues inside a transaction and drains on its commit, so the
        drain never claims runs the transaction has not committed. ``TestCase``
        never commits, hence the capture.
        """
        with self.captureOnCommitCallbacks(execute=True):
            call_command("run_pipeline", "--sample", stdout=io.StringIO())

        self.assertTrue(Alert.objects.exists())
        self.assertTrue(PipelineRun.objects.exists())
        self.assertFalse(PipelineRun.objects.filter(status=PipelineStatus.PENDING).exists())

    def test_sample_payload_is_analyzed_in_the_same_call(self):
        with self.captureOnCommitCallbacks(execute=True):
            call_command("run_pipeline", "--sample", stdout=io.StringIO())

        self.assertTrue(
            StageExecution.objects.filter(
                stage=PipelineStage.ANALYZE, status=StageStatus.SUCCEEDED
            ).exists()
        )

    def test_replayed_runs_are_manual(self):
        call_command("run_pipeline", "--sample", stdout=io.StringIO())

        self.assertEqual(PipelineRun.objects.first().origin, PipelineOrigin.MANUAL)

    def test_no_run_carries_a_driver_payload_wrapper(self):
        """The entry-stage era is over: a run's payload names its incident, only."""
        call_command("run_pipeline", "--sample", stdout=io.StringIO())

        self.assertTrue(PipelineRun.objects.exists())
        for run in PipelineRun.objects.all():
            self.assertIn("downstream_incident_id", run.inbound_payload)
            self.assertLessEqual(
                set(run.inbound_payload),
                {"downstream_incident_id", "no_notify"},
            )

    def test_payload_string_is_ingested(self):
        call_command(
            "run_pipeline",
            "--payload",
            '{"name": "Disk full", "status": "firing", "severity": "critical"}',
            stdout=io.StringIO(),
        )

        self.assertTrue(Alert.objects.filter(name="Disk full").exists())

    def test_file_payload_is_ingested(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"name": "From file", "status": "firing", "severity": "critical"}, f)
            payload_path = f.name
        try:
            call_command("run_pipeline", "--file", payload_path, stdout=io.StringIO())
        finally:
            os.unlink(payload_path)

        self.assertTrue(Alert.objects.filter(name="From file").exists())

    def test_source_selects_the_driver(self):
        """A named --source is the driver name, as it always was."""
        call_command("run_pipeline", "--sample", "--source", "alertmanager", stdout=io.StringIO())

        alert = Alert.objects.get(fingerprint="sample-cpu-alert-001")
        self.assertEqual(alert.source, "alertmanager")

    def test_json_output_reports_the_trace_and_its_incidents(self):
        out = io.StringIO()
        call_command("run_pipeline", "--sample", "--json", stdout=out)

        data = json.loads(out.getvalue())
        self.assertEqual(data["incidents"], [Alert.objects.get().incident_id])
        self.assertEqual(data["trace_id"], PipelineRun.objects.first().trace_id)
        self.assertEqual(data["alerts"], 1)
        self.assertEqual(data["errors"], [])

    def test_custom_trace_id_is_used(self):
        call_command("run_pipeline", "--sample", "--trace-id", "trace-abc", stdout=io.StringIO())

        self.assertEqual(PipelineRun.objects.first().trace_id, "trace-abc")

    def test_environment_reaches_the_run(self):
        call_command(
            "run_pipeline", "--sample", "--environment", "production", stdout=io.StringIO()
        )

        self.assertEqual(PipelineRun.objects.first().environment, "production")

    def test_unreadable_payload_is_a_command_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "run_pipeline", "--payload", '{"nothing": "recognisable"}', stdout=io.StringIO()
            )
        self.assertIn("driver", str(ctx.exception).lower())

    def test_ingest_failure_is_a_command_error(self):
        with mock.patch(
            "apps.alerts.services.AlertOrchestrator.process_webhook",
            side_effect=RuntimeError("unexpected"),
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("run_pipeline", "--sample", stdout=io.StringIO())
        self.assertIn("Pipeline failed", str(ctx.exception))

    def test_an_ingest_that_wrote_nothing_is_a_command_error(self):
        """A driver understood it and the batch still rolled back.

        Both views answer 5xx for this rather than pretending they accepted the
        payload; the operator's equivalent is a non-zero exit, not a summary
        reporting zero alerts as a success.
        """
        from apps.alerts.models import Alert

        with mock.patch(
            "apps.alerts.drivers.generic.GenericWebhookDriver.parse",
            side_effect=RuntimeError("transient bug"),
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "run_pipeline",
                    "--payload",
                    '{"name": "CPU high", "status": "firing", "severity": "critical"}',
                    stdout=io.StringIO(),
                )

        self.assertIn("transient bug", str(ctx.exception))
        self.assertFalse(Alert.objects.exists())

    def test_invalid_json_payload(self):
        with self.assertRaises(CommandError):
            call_command("run_pipeline", "--payload", "{invalid_json}", stdout=io.StringIO())

    def test_file_not_found(self):
        with self.assertRaises(CommandError):
            call_command("run_pipeline", "--file", "notfound.json", stdout=io.StringIO())

    def test_file_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        try:
            with self.assertRaises(CommandError) as ctx:
                call_command("run_pipeline", "--file", path, stdout=io.StringIO())
            self.assertIn("Invalid JSON in file", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_no_input(self):
        with self.assertRaises(CommandError):
            call_command("run_pipeline", stdout=io.StringIO())


@override_settings(INSTANCE_ID="solo-mac")
class RunPipelineChecksOnlyTests(StubAnalysisMixin, TestCase):
    """--checks-only is deprecated in favour of check_health, and still works."""

    def _run(self, *args, checker=None):
        out, err = StringIO(), StringIO()
        # The drain runs on the commit of the command's transaction; TestCase
        # never commits, so the callbacks are executed here instead.
        with patch.dict(REGISTRY_PATH, {"cpu": checker or make_checker()}, clear=True):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("run_pipeline", "--checks-only", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_checks_only_warns_that_it_is_deprecated(self):
        _, err = self._run()

        self.assertIn("deprecated", err.lower())
        self.assertIn("check_health", err)
        # Still does the work; a removed command is a worse surprise than a warning.
        self.assertTrue(Alert.objects.filter(fingerprint="check:solo-mac:cpu").exists())

    def test_deprecation_notice_stays_off_stdout(self):
        """--json exists so a wrapper can parse stdout; the warning must not break it."""
        out, _ = self._run("--json")

        json.loads(out)

    def test_checks_only_still_records_and_analyzes(self):
        self._run()

        alert = Alert.objects.get(fingerprint="check:solo-mac:cpu")
        self.assertIsNotNone(alert.incident)
        self.assertTrue(PipelineRun.objects.exists())
        self.assertFalse(PipelineRun.objects.filter(status=PipelineStatus.PENDING).exists())
        self.assertTrue(
            StageExecution.objects.filter(
                stage=PipelineStage.ANALYZE, status=StageStatus.SUCCEEDED
            ).exists()
        )

    def test_checks_only_runs_are_checker_generated(self):
        self._run()

        self.assertEqual(PipelineRun.objects.first().origin, PipelineOrigin.CHECKER_GENERATED)

    def test_no_incidents_records_alerts_but_enqueues_nothing(self):
        """ "Just check, do not disturb anything": no incident, so no run, so no route."""
        self._run("--no-incidents")

        alert = Alert.objects.get(fingerprint="check:solo-mac:cpu")
        self.assertIsNone(alert.incident)
        self.assertFalse(Incident.objects.exists())
        self.assertFalse(PipelineRun.objects.exists())
        self.assertFalse(StageExecution.objects.exists())

    def test_no_notify_reaches_the_enqueued_runs(self):
        """SSH in, read the analysis, page nobody."""
        self._run("--no-notify")

        run = PipelineRun.objects.get()
        self.assertTrue(run.inbound_payload.get("no_notify"))
        self.assertFalse(StageExecution.objects.filter(stage=PipelineStage.NOTIFY).exists())

    def test_notify_runs_without_the_flag(self):
        self._run()

        run = PipelineRun.objects.get()
        self.assertFalse(run.inbound_payload.get("no_notify", False))

    def test_named_checkers_are_the_only_ones_run(self):
        cpu, memory = make_checker(), make_checker(checker_name="memory")
        out, err = StringIO(), StringIO()
        with patch.dict(REGISTRY_PATH, {"cpu": cpu, "memory": memory}, clear=True):
            call_command(
                "run_pipeline",
                "--checks-only",
                "--checkers",
                "cpu",
                stdout=out,
                stderr=err,
            )

        cpu.assert_called_once()
        memory.assert_not_called()

    def test_hostname_overrides_the_alert_label(self):
        self._run("--hostname", "web-01")

        alert = Alert.objects.get(fingerprint="check:solo-mac:cpu")
        self.assertEqual(alert.labels["hostname"], "web-01")

    def test_labels_are_attached_to_the_alerts(self):
        self._run("--label", "env=production", "--label", "team=sre")

        alert = Alert.objects.get(fingerprint="check:solo-mac:cpu")
        self.assertEqual(alert.labels["env"], "production")
        self.assertEqual(alert.labels["team"], "sre")

    def test_invalid_label_format_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "run_pipeline", "--checks-only", "--label", "badlabel", stdout=io.StringIO()
            )
        self.assertIn("KEY=VALUE", str(ctx.exception))

    def test_thresholds_reach_every_checker(self):
        cpu = make_checker()
        with patch.dict(REGISTRY_PATH, {"cpu": cpu}, clear=True):
            call_command(
                "run_pipeline",
                "--checks-only",
                "--warning-threshold",
                "60",
                "--critical-threshold",
                "80",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        cpu.assert_called_once_with(warning_threshold=60.0, critical_threshold=80.0)

    def test_thresholds_reach_a_named_checker(self):
        cpu = make_checker()
        with patch.dict(REGISTRY_PATH, {"cpu": cpu}, clear=True):
            call_command(
                "run_pipeline",
                "--checks-only",
                "--checkers",
                "cpu",
                "--warning-threshold",
                "70",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        cpu.assert_called_once_with(warning_threshold=70.0)

    def test_critical_threshold_alone_reaches_a_named_checker(self):
        cpu = make_checker()
        with patch.dict(REGISTRY_PATH, {"cpu": cpu}, clear=True):
            call_command(
                "run_pipeline",
                "--checks-only",
                "--checkers",
                "cpu",
                "--critical-threshold",
                "90",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        cpu.assert_called_once_with(critical_threshold=90.0)

    def test_no_thresholds_leaves_checker_defaults(self):
        cpu = make_checker()
        with patch.dict(REGISTRY_PATH, {"cpu": cpu}, clear=True):
            call_command(
                "run_pipeline",
                "--checks-only",
                "--checkers",
                "cpu",
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        cpu.assert_called_once_with()

    def test_json_output_reports_the_trace_and_its_incidents(self):
        out, _ = self._run("--json")

        data = json.loads(out)
        self.assertEqual(data["incidents"], [Alert.objects.get().incident_id])
        self.assertEqual(data["checks"], 1)
        self.assertEqual(data["alerts"], 1)
        self.assertEqual(data["trace_id"], PipelineRun.objects.first().trace_id)

    def test_a_broken_checker_is_reported_and_does_not_abort(self):
        broken = MagicMock()
        broken.side_effect = RuntimeError("checker exploded")
        out, err = StringIO(), StringIO()
        with patch.dict(REGISTRY_PATH, {"cpu": broken}, clear=True):
            call_command("run_pipeline", "--checks-only", stdout=out, stderr=err)

        self.assertIn("checker exploded", err.getvalue())


class RunPipelineDryRunTests(TestCase):
    def test_dry_run_changes_nothing(self):
        out = io.StringIO()
        call_command("run_pipeline", "--sample", "--dry-run", stdout=out)

        self.assertIn("=== DRY RUN ===", out.getvalue())
        self.assertFalse(Alert.objects.exists())
        self.assertFalse(Incident.objects.exists())
        self.assertFalse(PipelineRun.objects.exists())

    def test_checks_only_dry_run_changes_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(REGISTRY_PATH, {"cpu": make_checker()}, clear=True):
            call_command("run_pipeline", "--checks-only", "--dry-run", stdout=out, stderr=err)

        self.assertIn("=== DRY RUN ===", out.getvalue())
        self.assertIn("check_health", err.getvalue())
        self.assertFalse(Alert.objects.exists())
        self.assertFalse(PipelineRun.objects.exists())

    def test_dry_run_still_validates_its_input(self):
        with self.assertRaises(CommandError):
            call_command(
                "run_pipeline", "--payload", "{invalid_json}", "--dry-run", stdout=io.StringIO()
            )


class RunPipelinePathTraversalTest(TestCase):
    """Tests for PathNotAllowedError handling in run_pipeline."""

    def test_file_path_rejected_outside_allowed_roots(self):
        """--file with disallowed path raises CommandError."""
        with self.assertRaises(CommandError):
            call_command("run_pipeline", "--file=/root/.ssh/id_rsa", stdout=io.StringIO())

    def test_file_not_found_raises_command_error(self):
        """--file with a valid but nonexistent path raises CommandError."""
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "run_pipeline",
                "--file=/tmp/nonexistent_pipeline_payload.json",
                stdout=io.StringIO(),
            )
        self.assertIn("File not found", str(ctx.exception))
