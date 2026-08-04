import io
import json
import os
import tempfile
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase


class RunPipelineCommandTest(TestCase):
    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_run_pipeline_with_sample(self, mock_orchestrator):
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "trace-123"
        mock_result.run_id = "run-123"
        mock_result.total_duration_ms = 123.45
        mock_result.ingest = {
            "incident_id": 1,
            "alerts_created": 1,
            "severity": "warning",
            "duration_ms": 10,
        }
        mock_result.check = {
            "checks_run": 2,
            "checks_passed": 2,
            "checks_failed": 0,
            "duration_ms": 5,
        }
        mock_result.analyze = {
            "summary": "ok",
            "probable_cause": "none",
            "recommendations": [],
            "duration_ms": 3,
        }
        mock_result.notify = {
            "channels_attempted": 1,
            "channels_succeeded": 1,
            "channels_failed": 0,
            "duration_ms": 2,
        }
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--sample", stdout=out)
        output = out.getvalue()
        self.assertIn("PIPELINE RESULT", output)
        self.assertIn("Status:", output)
        self.assertIn("✓ Pipeline completed successfully", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_run_pipeline_with_json_output(self, mock_orchestrator):
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.to_dict.return_value = {"status": "COMPLETED", "run_id": "run-1"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--sample", "--json", stdout=out)
        output = out.getvalue()
        self.assertIn('"status": "COMPLETED"', output)
        self.assertIn('"run_id": "run-1"', output)

    def test_run_pipeline_dry_run(self):
        out = io.StringIO()
        call_command("run_pipeline", "--sample", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn("=== DRY RUN ===", output)
        self.assertIn("Pipeline Configuration:", output)
        self.assertIn("Pipeline Stages:", output)

    def test_run_pipeline_invalid_json(self):
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("run_pipeline", "--payload", "{invalid_json}", stdout=out)

    def test_run_pipeline_file_not_found(self):
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("run_pipeline", "--file", "notfound.json", stdout=out)

    def test_run_pipeline_no_input(self):
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("run_pipeline", stdout=out)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_display_result_failed_pipeline(self, mock_orchestrator):
        """Failed pipeline shows error status."""
        mock_result = mock.Mock()
        mock_result.status = "FAILED"
        mock_result.trace_id = "trace-err"
        mock_result.run_id = "run-err"
        mock_result.total_duration_ms = 50.0
        mock_result.ingest = None
        mock_result.check = None
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = ["something broke"]
        mock_result.final_error = None
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--sample", stdout=out)
        output = out.getvalue()
        self.assertIn("FAILED", output)
        self.assertIn("Pipeline failed", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_display_result_with_analyze_fallback(self, mock_orchestrator):
        """Display shows fallback warning when AI is unavailable."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 10
        mock_result.ingest = None
        mock_result.check = None
        mock_result.analyze = {
            "summary": "Fallback analysis",
            "probable_cause": "Unknown",
            "recommendations": [],
            "fallback_used": True,
            "duration_ms": 1,
        }
        mock_result.notify = None
        mock_result.errors = []
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--sample", stdout=out)
        output = out.getvalue()
        self.assertIn("Fallback used", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_run_pipeline_with_payload_string(self, mock_orchestrator):
        """Runs pipeline with --payload JSON string."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 10
        mock_result.ingest = None
        mock_result.check = None
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command(
            "run_pipeline",
            "--payload",
            '{"title": "Test Alert"}',
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("PIPELINE RESULT", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_run_pipeline_with_file_payload(self, mock_orchestrator):
        """Runs pipeline with --file payload."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 10
        mock_result.ingest = None
        mock_result.check = None
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"title": "File Alert"}, f)
            payload_path = f.name

        try:
            out = io.StringIO()
            call_command("run_pipeline", "--file", payload_path, stdout=out)
            output = out.getvalue()
            self.assertIn("PIPELINE RESULT", output)
        finally:
            os.unlink(payload_path)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_run_pipeline_checks_only(self, mock_orchestrator):
        """Runs pipeline with --checks-only flag."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 10
        mock_result.ingest = None
        mock_result.check = {"checks_run": 3, "checks_passed": 3, "checks_failed": 0}
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--checks-only", stdout=out)
        output = out.getvalue()
        self.assertIn("PIPELINE RESULT", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_generic_exception_wrapped_as_command_error(self, mock_orchestrator):
        """Non-CommandError exceptions are wrapped in CommandError."""
        mock_orchestrator.return_value.run_pipeline.side_effect = RuntimeError("unexpected")
        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("run_pipeline", "--sample", stdout=out)
        self.assertIn("Pipeline failed", str(ctx.exception))

    def test_run_pipeline_file_invalid_json(self):
        """--file with invalid JSON raises CommandError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        try:
            with self.assertRaises(CommandError) as ctx:
                call_command("run_pipeline", "--file", path, stdout=io.StringIO())
            self.assertIn("Invalid JSON in file", str(ctx.exception))
        finally:
            os.unlink(path)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_display_result_notify_stage_with_to_dict_and_errors(self, mock_orchestrator):
        """NOTIFY stage with to_dict() objects and errors displayed."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 10
        # Use mock with to_dict() for ingest (covers line 341)
        ingest_stage = mock.Mock()
        ingest_stage.to_dict.return_value = {
            "incident_id": 1,
            "alerts_created": 1,
            "severity": "warning",
            "duration_ms": 5,
        }
        mock_result.ingest = ingest_stage
        mock_result.check = None
        mock_result.analyze = None
        # NOTIFY stage as dict with errors (covers 424→432, 434)
        mock_result.notify = {
            "channels_attempted": 1,
            "channels_succeeded": 0,
            "channels_failed": 1,
            "errors": ["Channel failed"],
            "duration_ms": 5,
        }
        mock_result.errors = []
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result
        out = io.StringIO()
        call_command("run_pipeline", "--sample", stdout=out)
        output = out.getvalue()
        self.assertIn("Channels attempted: 1", output)
        self.assertIn("Errors:", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_display_result_failed_with_stack_trace(self, mock_orchestrator):
        """Failed pipeline with final_error containing stack_trace."""
        mock_result = mock.Mock()
        mock_result.status = "FAILED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 10
        mock_result.ingest = None
        mock_result.check = None
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = ["error"]
        final_error = mock.Mock()
        final_error.error_type = "RuntimeError"
        final_error.message = "something broke"
        final_error.stack_trace = "Traceback:\n  File ..."
        mock_result.final_error = final_error
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result
        out = io.StringIO()
        call_command("run_pipeline", "--sample", stdout=out)
        output = out.getvalue()
        self.assertIn("RuntimeError", output)
        self.assertIn("something broke", output)
        self.assertIn("Traceback:", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_display_result_failed_final_error_raises_exception(self, mock_orchestrator):
        """Failed pipeline where accessing final_error attributes raises falls back to str()."""
        mock_result = mock.Mock()
        mock_result.status = "FAILED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 10
        mock_result.ingest = None
        mock_result.check = None
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = ["error"]

        # final_error whose attribute access raises, falling back to str()
        class BadError:
            @property
            def error_type(self):
                raise RuntimeError("cannot access")

            def __str__(self):
                return "fallback error text"

        mock_result.final_error = BadError()
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result
        out = io.StringIO()
        call_command("run_pipeline", "--sample", stdout=out)
        output = out.getvalue()
        self.assertIn("fallback error text", output)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checks_only_with_checkers_flag(self, mock_orchestrator):
        """--checks-only with --checkers passes checker_names in payload."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--checks-only", "--checkers", "cpu", "memory", stdout=out)

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertEqual(payload["checker_names"], ["cpu", "memory"])

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checks_only_with_hostname_flag(self, mock_orchestrator):
        """--hostname is passed through the payload."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--checks-only", "--hostname", "web-01", stdout=out)

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertEqual(payload["hostname"], "web-01")

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checks_only_with_labels(self, mock_orchestrator):
        """--label KEY=VALUE flags are parsed and passed in payload."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command(
            "run_pipeline",
            "--checks-only",
            "--label",
            "env=production",
            "--label",
            "team=sre",
            stdout=out,
        )

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertEqual(payload["labels"], {"env": "production", "team": "sre"})

    def test_invalid_label_format_raises_error(self):
        """--label without = raises CommandError."""
        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("run_pipeline", "--checks-only", "--label", "badlabel", stdout=out)
        self.assertIn("KEY=VALUE", str(ctx.exception))

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checks_only_with_no_incidents(self, mock_orchestrator):
        """--no-incidents flag is passed in payload."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--checks-only", "--no-incidents", stdout=out)

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertTrue(payload["no_incidents"])

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checks_only_with_threshold_overrides(self, mock_orchestrator):
        """--warning-threshold and --critical-threshold are passed as checker_configs."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command(
            "run_pipeline",
            "--checks-only",
            "--warning-threshold",
            "60",
            "--critical-threshold",
            "80",
            stdout=out,
        )

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertTrue(payload["checks_only"])
        self.assertIn("__all__", payload["checker_configs"])
        self.assertEqual(payload["checker_configs"]["__all__"]["warning_threshold"], 60.0)
        self.assertEqual(payload["checker_configs"]["__all__"]["critical_threshold"], 80.0)

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checkers_with_only_warning_threshold(self, mock_orchestrator):
        """--checkers with only --warning-threshold sets per-checker config."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command(
            "run_pipeline",
            "--checks-only",
            "--checkers",
            "cpu",
            "--warning-threshold",
            "70",
            stdout=out,
        )

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertEqual(payload["checker_configs"]["cpu"], {"warning_threshold": 70.0})

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checkers_with_only_critical_threshold(self, mock_orchestrator):
        """--checkers with only --critical-threshold sets per-checker config."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command(
            "run_pipeline",
            "--checks-only",
            "--checkers",
            "cpu",
            "--critical-threshold",
            "90",
            stdout=out,
        )

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertEqual(payload["checker_configs"]["cpu"], {"critical_threshold": 90.0})

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_checkers_without_thresholds(self, mock_orchestrator):
        """--checkers without thresholds produces no checker_configs."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command("run_pipeline", "--checks-only", "--checkers", "cpu", "disk", stdout=out)

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertIsNone(payload["checker_configs"])

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_only_warning_threshold_without_checkers(self, mock_orchestrator):
        """--warning-threshold alone sets __all__ with only warning."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command(
            "run_pipeline",
            "--checks-only",
            "--warning-threshold",
            "65",
            stdout=out,
        )

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertEqual(payload["checker_configs"]["__all__"], {"warning_threshold": 65.0})
        self.assertNotIn("critical_threshold", payload["checker_configs"]["__all__"])

    @mock.patch("apps.orchestration.management.commands.run_pipeline.PipelineOrchestrator")
    def test_only_critical_threshold_without_checkers(self, mock_orchestrator):
        """--critical-threshold alone sets __all__ with only critical."""
        mock_result = mock.Mock()
        mock_result.status = "COMPLETED"
        mock_result.trace_id = "t"
        mock_result.run_id = "r"
        mock_result.total_duration_ms = 1.0
        mock_result.ingest = None
        mock_result.check = {
            "checks_run": 1,
            "checks_passed": 1,
            "checks_failed": 0,
            "duration_ms": 1,
        }
        mock_result.analyze = None
        mock_result.notify = None
        mock_result.errors = []
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator.return_value.run_pipeline.return_value = mock_result

        out = io.StringIO()
        call_command(
            "run_pipeline",
            "--checks-only",
            "--critical-threshold",
            "90",
            stdout=out,
        )

        call_args = mock_orchestrator.return_value.run_pipeline.call_args
        payload = call_args[1]["payload"] if "payload" in call_args[1] else call_args[0][0]
        self.assertEqual(payload["checker_configs"]["__all__"], {"critical_threshold": 90.0})
        self.assertNotIn("warning_threshold", payload["checker_configs"]["__all__"])


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
