"""Tests for monitor_pipeline management command — pipeline detail integration."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.orchestration.models import PipelineDefinition, PipelineRun


class ShowRunDetailsWithPipelineDefinitionTests(TestCase):
    """Tests for pipeline definition display in show_run_details."""

    def setUp(self):
        self.run = PipelineRun.objects.create(
            run_id="test-run-001",
            trace_id="trace-001",
            source="test",
        )

    def test_run_details_includes_pipeline_definitions(self):
        """When pipeline definitions exist, show_run_details renders them."""
        PipelineDefinition.objects.create(
            name="my-pipeline",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "check",
                        "type": "context",
                        "config": {"checker_names": ["cpu"]},
                    },
                ],
            },
        )
        out = StringIO()
        call_command("monitor_pipeline", "--run-id", "test-run-001", stdout=out)
        output = out.getvalue()
        assert "my-pipeline" in output
        assert "cpu" in output

    def test_run_details_without_pipeline_definitions(self):
        """When no pipeline definitions exist, show_run_details still works."""
        out = StringIO()
        call_command("monitor_pipeline", "--run-id", "test-run-001", stdout=out)
        output = out.getvalue()
        assert "test-run-001" in output
        assert "Pipeline Run:" in output
