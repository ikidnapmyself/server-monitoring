import io
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase


class RunPipelineCommandTest(TestCase):
    @mock.patch("apps.orchestration.orchestrator.PipelineOrchestrator")
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

    @mock.patch("apps.orchestration.orchestrator.PipelineOrchestrator")
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

    def test_run_pipeline_definition_dry_run(self):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-pipeline",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "ctx",
                        "type": "context",
                        "config": {"include": ["cpu"]},
                        "next": "notify",
                    },
                    {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
                ],
            },
        )

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-pipeline", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn("=== DRY RUN ===", output)
        self.assertIn("Pipeline Definition: test-pipeline", output)
        self.assertIn("[context] ctx", output)
        self.assertIn("[notify] notify", output)

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

    def test_run_pipeline_definition_not_found(self):
        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("run_pipeline", "--definition", "nonexistent", stdout=out)
        self.assertIn("Pipeline definition not found", str(ctx.exception))

    def test_run_pipeline_config_file_not_found(self):
        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("run_pipeline", "--config", "missing.json", stdout=out)
        self.assertIn("Config file not found", str(ctx.exception))

    def test_run_pipeline_definition_and_config_mutually_exclusive(self):
        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "run_pipeline", "--definition", "test", "--config", "test.json", stdout=out
            )
        self.assertIn("Cannot specify both", str(ctx.exception))
