# apps/orchestration/_tests/test_integration.py
"""Integration tests for the complete pipeline system."""

from django.test import TestCase

from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition, PipelineRun


class TestPipelineIntegration(TestCase):
    """Integration tests for complete pipelines."""

    def test_context_to_intelligence_pipeline(self):
        """Test a pipeline that gathers context and runs intelligence."""
        definition = PipelineDefinition.objects.create(
            name="context-intelligence",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "gather",
                        "type": "context",
                        "config": {"checker_names": ["cpu", "memory"]},
                        "next": "analyze",
                    },
                    {
                        "id": "analyze",
                        "type": "intelligence",
                        "config": {"provider": "local"},
                    },
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="test")

        assert result["status"] == "completed"
        assert "gather" in result["executed_nodes"]
        assert "analyze" in result["executed_nodes"]
        # Context node outputs checker results
        gather_output = result["node_results"]["gather"]["output"]
        assert "checks_run" in gather_output
        assert "results" in gather_output

    def test_pipeline_with_optional_failing_node(self):
        """Test that optional nodes don't break the pipeline."""
        definition = PipelineDefinition.objects.create(
            name="optional-fail",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "context",
                        "type": "context",
                        "config": {"checker_names": ["cpu"]},
                        "next": "bad_notify",
                    },
                    {
                        "id": "bad_notify",
                        "type": "notify",
                        "required": False,  # Optional - won't fail pipeline
                        "config": {
                            "driver": "generic",
                            "driver_config": {"endpoint": "http://invalid.invalid"},
                        },
                    },
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="test")

        # Pipeline should complete despite notify failure
        assert result["status"] in ("completed", "partial")
        assert "context" in result["executed_nodes"]

    def test_transform_between_nodes(self):
        """Test transform node processes data between stages."""
        definition = PipelineDefinition.objects.create(
            name="with-transform",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "context",
                        "type": "context",
                        "config": {"checker_names": ["cpu", "memory"]},
                        "next": "transform",
                    },
                    {
                        "id": "transform",
                        "type": "transform",
                        "config": {
                            "source_node": "context",
                            "mapping": {
                                "cpu_status": "results.cpu.status",
                                "mem_status": "results.memory.status",
                            },
                        },
                        "next": "analyze",
                    },
                    {
                        "id": "analyze",
                        "type": "intelligence",
                        "config": {"provider": "local"},
                    },
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="test")

        assert result["status"] == "completed"
        transform_output = result["node_results"]["transform"]["output"]
        assert "transformed" in transform_output

    def test_pipeline_creates_run_record(self):
        """Test that pipeline execution creates proper records."""
        definition = PipelineDefinition.objects.create(
            name="record-test",
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

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="integration-test")

        # Verify PipelineRun was created
        run = PipelineRun.objects.get(run_id=result["run_id"])
        assert run.source == "integration-test"
        assert run.status in ("notified", "completed")

        # Verify stage executions were created
        stages = run.stage_executions.all()
        assert stages.count() >= 1
