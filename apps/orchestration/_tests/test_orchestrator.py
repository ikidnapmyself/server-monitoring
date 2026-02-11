"""Tests for PipelineOrchestrator."""

from unittest.mock import patch

from django.test import TestCase

from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
)
from apps.orchestration.models import PipelineStage, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator, StageExecutionError


class OrchestratorTests(TestCase):
    """Test PipelineOrchestrator."""

    def test_start_pipeline_creates_run(self):
        """Test that start_pipeline creates a PipelineRun."""
        orchestrator = PipelineOrchestrator()
        payload = {"payload": {}}
        run = orchestrator.start_pipeline(payload, source="test")

        assert run.id is not None
        assert run.trace_id is not None
        assert run.run_id is not None
        assert run.source == "test"
        assert run.status == PipelineStatus.PENDING

    def test_start_pipeline_uses_provided_trace_id(self):
        """Test that start_pipeline uses provided trace_id."""
        orchestrator = PipelineOrchestrator()
        payload = {"payload": {}}
        run = orchestrator.start_pipeline(
            payload,
            source="test",
            trace_id="custom-trace-id",
        )

        assert run.trace_id == "custom-trace-id"

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_run_pipeline_full_flow(self, mock_execute):
        """Test full pipeline execution flow."""
        # Mock stage results - incident_id=None to avoid FK issues
        mock_execute.side_effect = [
            IngestResult(incident_id=None, alerts_created=1),
            CheckResult(checks_run=2),
            AnalyzeResult(summary="Test summary"),
            NotifyResult(channels_succeeded=1),
        ]

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(
            payload={"payload": {}},
            source="test",
        )

        assert result.status == "COMPLETED"
        assert len(result.stages_completed) == 4
        assert PipelineStage.INGEST in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_run_pipeline_with_fallback(self, mock_execute):
        """Test pipeline with intelligence fallback."""
        # Mock stage results with fallback analyze - incident_id=None
        mock_execute.side_effect = [
            IngestResult(incident_id=None),
            CheckResult(checks_run=1),
            AnalyzeResult(summary="AI unavailable", fallback_used=True),
            NotifyResult(channels_succeeded=1),
        ]

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(
            payload={"payload": {}},
            source="test",
        )

        assert result.status == "COMPLETED"
        assert result.analyze.fallback_used is True

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_run_pipeline_stage_failure(self, mock_execute):
        """Test pipeline failure handling."""
        # Mock first stage to fail
        mock_execute.side_effect = StageExecutionError(
            stage=PipelineStage.INGEST,
            errors=["Test error"],
            retryable=False,
        )

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(
            payload={"payload": {}},
            source="test",
        )

        assert result.status == "FAILED"
        assert result.final_error is not None
        assert "Test error" in result.final_error.message


class StageExecutionErrorTests(TestCase):
    """Test StageExecutionError."""

    def test_stage_execution_error(self):
        """Test StageExecutionError creation."""
        error = StageExecutionError(
            stage="ingest",
            errors=["Error 1", "Error 2"],
            retryable=True,
        )
        assert error.stage == "ingest"
        assert len(error.errors) == 2
        assert error.retryable is True
        assert "ingest" in str(error)
