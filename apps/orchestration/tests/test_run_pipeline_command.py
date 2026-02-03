import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
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


@pytest.mark.django_db
class TestSamplePipelineDefinitions:
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
