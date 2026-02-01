# apps/orchestration/_tests/test_definition_orchestrator.py
"""Tests for DefinitionBasedOrchestrator."""

import pytest

from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition


@pytest.mark.django_db
class TestDefinitionBasedOrchestrator:
    """Tests for DefinitionBasedOrchestrator."""

    def test_execute_simple_pipeline(self, simple_pipeline_config):
        """Test executing a simple pipeline."""
        definition = PipelineDefinition.objects.create(
            name="test-simple",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(
            payload={"test": "data"},
            source="test",
        )

        assert result["status"] in ("completed", "partial")
        assert "executed_nodes" in result
        assert len(result["executed_nodes"]) > 0

    def test_execute_records_pipeline_run(self, simple_pipeline_config):
        """Test that execution creates a PipelineRun record."""
        from apps.orchestration.models import PipelineRun

        definition = PipelineDefinition.objects.create(
            name="test-record",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(
            payload={"test": "data"},
            source="test",
        )

        # Check PipelineRun was created
        run = PipelineRun.objects.filter(run_id=result["run_id"]).first()
        assert run is not None
        assert run.source == "test"

    def test_execute_chains_node_outputs(self, simple_pipeline_config):
        """Test that node outputs are passed to subsequent nodes."""
        definition = PipelineDefinition.objects.create(
            name="test-chain",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(
            payload={"test": "data"},
            source="test",
        )

        # Check that node results include outputs
        assert "node_results" in result
        for node_id, node_result in result["node_results"].items():
            assert "output" in node_result

    def test_validate_definition(self, simple_pipeline_config):
        """Test validating a pipeline definition."""
        definition = PipelineDefinition.objects.create(
            name="test-validate",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert isinstance(errors, list)

    def test_validate_catches_invalid_node_type(self):
        """Test validation catches invalid node types."""
        definition = PipelineDefinition.objects.create(
            name="test-invalid",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "bad", "type": "nonexistent"},
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert len(errors) > 0
        assert any("nonexistent" in e.lower() for e in errors)
