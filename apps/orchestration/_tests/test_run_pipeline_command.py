import io
import json
import os
import tempfile
from pathlib import Path
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

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_run_pipeline_with_definition(self, mock_execute, mock_validate):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-exec-pipeline",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "trace-456",
            "run_id": "run-456",
            "definition": "test-exec-pipeline",
            "definition_version": 1,
            "status": "completed",
            "executed_nodes": ["notify"],
            "skipped_nodes": [],
            "node_results": {
                "notify": {"node_id": "notify", "node_type": "notify", "duration_ms": 50}
            },
            "duration_ms": 100.0,
            "error": None,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-exec-pipeline", stdout=out)
        output = out.getvalue()
        self.assertIn("PIPELINE RESULT", output)
        self.assertIn("Definition: test-exec-pipeline", output)
        self.assertIn("completed", output.lower())

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

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_run_pipeline_with_config_file(self, mock_execute, mock_validate):
        config = {
            "version": "1.0",
            "nodes": [
                {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            config_path = f.name

        try:
            mock_validate.return_value = []
            mock_execute.return_value = {
                "trace_id": "trace-789",
                "run_id": "run-789",
                "definition": f"__adhoc__{config_path}",
                "definition_version": 1,
                "status": "completed",
                "executed_nodes": ["notify"],
                "skipped_nodes": [],
                "node_results": {},
                "duration_ms": 50.0,
                "error": None,
            }

            out = io.StringIO()
            call_command("run_pipeline", "--config", config_path, stdout=out)
            output = out.getvalue()
            self.assertIn("PIPELINE RESULT", output)
            self.assertIn("completed", output.lower())
        finally:
            os.unlink(config_path)

    def test_run_pipeline_invalid_config_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json content")
            config_path = f.name

        try:
            out = io.StringIO()
            with self.assertRaises(CommandError) as ctx:
                call_command("run_pipeline", "--config", config_path, stdout=out)
            self.assertIn("Invalid JSON in config file", str(ctx.exception))
        finally:
            os.unlink(config_path)

    def test_run_pipeline_definition_validation_error(self):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="invalid-pipeline",
            config={
                # Missing version and has invalid node type
                "nodes": [
                    {"id": "bad", "type": "nonexistent_type"},
                ]
            },
        )

        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("run_pipeline", "--definition", "invalid-pipeline", stdout=out)
        self.assertIn("Pipeline definition invalid", str(ctx.exception))

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

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_result_context_node(self, mock_execute, mock_validate):
        """Definition result display shows context node details."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-ctx-display",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}},
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "t",
            "run_id": "r",
            "definition": "test-ctx-display",
            "status": "completed",
            "executed_nodes": ["ctx"],
            "skipped_nodes": [],
            "node_results": {
                "ctx": {
                    "node_id": "ctx",
                    "node_type": "context",
                    "output": {
                        "checks_run": 3,
                        "checks_passed": 2,
                        "checks_failed": 1,
                        "results": {
                            "cpu": {"status": "ok", "message": "CPU fine"},
                            "memory": {"status": "warning", "message": "Memory high"},
                            "disk": {"status": "ok", "message": "Disk fine"},
                        },
                    },
                    "errors": [],
                    "duration_ms": 50.0,
                },
            },
            "duration_ms": 100.0,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-ctx-display", stdout=out)
        output = out.getvalue()
        self.assertIn("Checks run: 3", output)
        self.assertIn("Passed: 2", output)
        self.assertIn("Failed: 1", output)
        self.assertIn("memory: warning", output)
        self.assertIn("cpu: ok", output)

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_result_notify_node(self, mock_execute, mock_validate):
        """Definition result display shows notify node details."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-notify-display",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "t",
            "run_id": "r",
            "definition": "test-notify-display",
            "status": "completed",
            "executed_nodes": ["notify"],
            "skipped_nodes": [],
            "node_results": {
                "notify": {
                    "node_id": "notify",
                    "node_type": "notify",
                    "output": {
                        "channels_attempted": 2,
                        "channels_succeeded": 1,
                        "channels_failed": 1,
                        "deliveries": [
                            {
                                "driver": "slack",
                                "channel": "ops-alerts",
                                "status": "success",
                            },
                            {
                                "driver": "email",
                                "channel": "ops-email",
                                "status": "failed",
                                "error": "SMTP timeout",
                            },
                        ],
                    },
                    "errors": [],
                    "duration_ms": 200.0,
                },
            },
            "duration_ms": 250.0,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-notify-display", stdout=out)
        output = out.getvalue()
        self.assertIn("Channels attempted: 2", output)
        self.assertIn("Succeeded: 1", output)
        self.assertIn("Failed: 1", output)
        self.assertIn("slack (ops-alerts): sent", output)
        self.assertIn("email (ops-email): SMTP timeout", output)

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_result_intelligence_node(self, mock_execute, mock_validate):
        """Definition result display shows intelligence node details."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-intel-display",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "analyze",
                        "type": "intelligence",
                        "config": {"provider": "local"},
                    },
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "t",
            "run_id": "r",
            "definition": "test-intel-display",
            "status": "completed",
            "executed_nodes": ["analyze"],
            "skipped_nodes": [],
            "node_results": {
                "analyze": {
                    "node_id": "analyze",
                    "node_type": "intelligence",
                    "output": {
                        "summary": "High CPU caused by worker loop",
                        "provider": "local",
                    },
                    "errors": [],
                    "duration_ms": 30.0,
                },
            },
            "duration_ms": 50.0,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-intel-display", stdout=out)
        output = out.getvalue()
        self.assertIn("Summary: High CPU caused by worker loop", output)
        self.assertIn("Provider: local", output)

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_result_skipped_node(self, mock_execute, mock_validate):
        """Definition result display shows skipped nodes."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-skip-display",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}},
                    {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "t",
            "run_id": "r",
            "definition": "test-skip-display",
            "status": "completed",
            "executed_nodes": ["notify"],
            "skipped_nodes": ["ctx"],
            "node_results": {
                "notify": {
                    "node_id": "notify",
                    "node_type": "notify",
                    "output": {"channels_attempted": 0},
                    "errors": [],
                    "duration_ms": 10.0,
                },
            },
            "duration_ms": 50.0,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-skip-display", stdout=out)
        output = out.getvalue()
        self.assertIn("(skipped)", output)

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_result_with_errors(self, mock_execute, mock_validate):
        """Definition result display shows node errors."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-err-display",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "t",
            "run_id": "r",
            "definition": "test-err-display",
            "status": "partial",
            "executed_nodes": ["notify"],
            "skipped_nodes": [],
            "node_results": {
                "notify": {
                    "node_id": "notify",
                    "node_type": "notify",
                    "output": {
                        "channels_attempted": 1,
                        "channels_succeeded": 0,
                        "channels_failed": 1,
                    },
                    "errors": ["All 1 notification channel(s) failed"],
                    "duration_ms": 10.0,
                },
            },
            "duration_ms": 50.0,
            "error": None,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-err-display", stdout=out)
        output = out.getvalue()
        self.assertIn("Errors:", output)
        self.assertIn("Pipeline failed", output)

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_result_ingest_node(self, mock_execute, mock_validate):
        """Definition result display shows ingest node details."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-ingest-display",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ingest", "type": "ingest", "config": {}},
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "t",
            "run_id": "r",
            "definition": "test-ingest-display",
            "status": "completed",
            "executed_nodes": ["ingest"],
            "skipped_nodes": [],
            "node_results": {
                "ingest": {
                    "node_id": "ingest",
                    "node_type": "ingest",
                    "output": {
                        "incident_id": 42,
                        "alerts_created": 1,
                    },
                    "errors": [],
                    "duration_ms": 15.0,
                },
            },
            "duration_ms": 30.0,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-ingest-display", stdout=out)
        output = out.getvalue()
        self.assertIn("Incident ID: 42", output)
        self.assertIn("Alerts created: 1", output)

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_json_output(self, mock_execute, mock_validate):
        """Definition result with --json outputs JSON."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="test-json-def",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
                ],
            },
        )

        mock_validate.return_value = []
        mock_execute.return_value = {
            "trace_id": "t-json",
            "run_id": "r-json",
            "definition": "test-json-def",
            "status": "completed",
            "executed_nodes": ["notify"],
            "skipped_nodes": [],
            "node_results": {},
            "duration_ms": 10.0,
        }

        out = io.StringIO()
        call_command("run_pipeline", "--definition", "test-json-def", "--json", stdout=out)
        output = out.getvalue()
        self.assertIn('"status": "completed"', output)
        self.assertIn('"trace_id": "t-json"', output)

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

    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.validate")
    @mock.patch("apps.orchestration.definition_orchestrator.DefinitionBasedOrchestrator.execute")
    def test_display_definition_result_from_config_file(self, mock_execute, mock_validate):
        """Definition result display shows config path when loaded from file."""
        config = {
            "version": "1.0",
            "nodes": [
                {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            config_path = f.name

        try:
            mock_validate.return_value = []
            mock_execute.return_value = {
                "trace_id": "t",
                "run_id": "r",
                "definition": f"__adhoc__{config_path}",
                "status": "completed",
                "executed_nodes": ["notify"],
                "skipped_nodes": [],
                "node_results": {
                    "notify": {
                        "node_id": "notify",
                        "node_type": "notify",
                        "output": {"channels_attempted": 0},
                        "errors": [],
                        "duration_ms": 10.0,
                    },
                },
                "duration_ms": 20.0,
            }

            out = io.StringIO()
            call_command("run_pipeline", "--config", config_path, stdout=out)
            output = out.getvalue()
            self.assertIn("Config:", output)
        finally:
            os.unlink(config_path)


class TestSamplePipelineDefinitions(TestCase):
    """Tests for apps/orchestration/management/commands/pipelines/ sample definition files."""

    def _load_and_validate_pipeline(self, filename: str, name: str):
        """Load a pipeline JSON file and validate it.

        Args:
            filename: The JSON filename (e.g., "pipeline-manager.json")
            name: The name to assign to the PipelineDefinition

        Returns:
            A tuple of (definition, config) where definition is a validated
            PipelineDefinition instance and config is the raw JSON dict.
        """
        from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
        from apps.orchestration.models import PipelineDefinition

        config_path = Path(f"apps/orchestration/management/commands/pipelines/{filename}")
        assert config_path.exists(), f"Missing {config_path}"

        with open(config_path) as f:
            config = json.load(f)

        definition = PipelineDefinition(name=name, config=config)
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert errors == [], f"Validation errors: {errors}"
        return definition, config

    def test_pipeline_manager_json_is_valid(self):
        """Verify pipeline-manager.json can be loaded and validated."""
        definition, _ = self._load_and_validate_pipeline("pipeline-manager.json", "test-pm")

        assert len(definition.get_nodes()) == 3
        assert definition.get_nodes()[0]["type"] == "ingest"
        assert definition.get_nodes()[1]["type"] == "intelligence"
        assert definition.get_nodes()[2]["type"] == "notify"

    def test_local_monitor_json_is_valid(self):
        """Verify local-monitor.json can be loaded and validated."""
        definition, _ = self._load_and_validate_pipeline("local-monitor.json", "test-lm")

        assert len(definition.get_nodes()) == 4
        node_types = [n["type"] for n in definition.get_nodes()]
        assert node_types == ["ingest", "context", "intelligence", "notify"]

    def test_pipeline_manager_openai_json_is_valid(self):
        """Verify pipeline-manager-openai.json can be loaded and validated."""
        definition, _ = self._load_and_validate_pipeline(
            "pipeline-manager-openai.json", "test-pm-openai"
        )

        # Verify OpenAI provider config
        analyze_node = definition.get_nodes()[1]
        assert analyze_node["config"]["provider"] == "openai"
        assert analyze_node["config"]["provider_config"]["model"] == "gpt-4o-mini"

    def test_pagerduty_alert_json_is_valid(self):
        """Verify pagerduty-alert.json can be loaded and validated."""
        definition, config = self._load_and_validate_pipeline(
            "pagerduty-alert.json", "test-pd-alert"
        )

        assert len(definition.get_nodes()) == 3
        # Verify context_flow documentation exists
        assert "context_flow" in config
        # Verify ingest has source_hint
        ingest_node = definition.get_nodes()[0]
        assert ingest_node["config"].get("source_hint") == "pagerduty"
