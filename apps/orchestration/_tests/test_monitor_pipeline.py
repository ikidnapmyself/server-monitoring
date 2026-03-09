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


class TestListRuns(TestCase):
    """Tests for list_runs output."""

    def test_list_runs_shows_table(self):
        """list_runs displays pipeline runs in table format."""
        PipelineRun.objects.create(
            run_id="run-001",
            trace_id="trace-001",
            source="test",
            status="completed",
        )
        out = StringIO()
        call_command("monitor_pipeline", stdout=out)
        output = out.getvalue()
        assert "run-001" in output
        assert "trace-001" in output
        assert "completed" in output

    def test_list_runs_empty(self):
        """list_runs shows warning when no runs exist."""
        out = StringIO()
        call_command("monitor_pipeline", stdout=out)
        output = out.getvalue()
        assert "No pipeline runs found" in output

    def test_list_runs_filtered_by_status(self):
        """list_runs filters by --status flag."""
        PipelineRun.objects.create(
            run_id="run-ok",
            trace_id="t",
            source="test",
            status="completed",
        )
        PipelineRun.objects.create(
            run_id="run-fail",
            trace_id="t",
            source="test",
            status="failed",
        )
        out = StringIO()
        call_command("monitor_pipeline", "--status", "failed", stdout=out)
        output = out.getvalue()
        assert "run-fail" in output
        assert "run-ok" not in output


class TestShowRunDetails(TestCase):
    """Tests for show_run_details edge cases."""

    def test_show_run_details_not_found(self):
        """show_run_details shows error for nonexistent run_id."""
        out = StringIO()
        call_command("monitor_pipeline", "--run-id", "nonexistent", stdout=out)
        output = out.getvalue()
        assert "Pipeline run not found" in output

    def test_show_run_details_with_error_and_stages(self):
        """show_run_details displays last_error_message and stage errors."""
        run = PipelineRun.objects.create(
            run_id="run-err",
            trace_id="trace-err",
            source="test",
            status="failed",
            last_error_message="Pipeline timeout",
        )
        run.stage_executions.create(
            stage="ingest",
            status="completed",
            attempt=1,
            duration_ms=10.0,
        )
        run.stage_executions.create(
            stage="analyze",
            status="failed",
            attempt=1,
            duration_ms=5.0,
            error_message="Provider unavailable",
        )
        out = StringIO()
        call_command("monitor_pipeline", "--run-id", "run-err", stdout=out)
        output = out.getvalue()
        assert "Pipeline timeout" in output
        assert "Provider unavailable" in output
        assert "ingest" in output
        assert "analyze" in output
