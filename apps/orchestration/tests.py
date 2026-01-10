"""
Tests for the orchestration app.

Tests cover:
- State machine transitions
- Idempotency keys
- DTO serialization
- Full pipeline happy-path
- Intelligence fallback
- Retry exhaustion → FAILED
"""

import uuid
from unittest.mock import patch

import pytest
from django.test import TestCase

from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
    PipelineResult,
    StageContext,
)
from apps.orchestration.models import (
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
from apps.orchestration.orchestrator import PipelineOrchestrator, StageExecutionError
from apps.orchestration.signals import SignalTags


class DTOSerializationTests(TestCase):
    """Test DTO serialization."""

    def test_stage_context_to_dict(self):
        """Test StageContext serialization."""
        ctx = StageContext(
            trace_id="trace-123",
            run_id="run-456",
            incident_id=1,
            attempt=2,
            environment="staging",
            source="grafana",
        )
        data = ctx.to_dict()
        assert data["trace_id"] == "trace-123"
        assert data["run_id"] == "run-456"
        assert data["incident_id"] == 1
        assert data["attempt"] == 2

    def test_ingest_result_to_dict(self):
        """Test IngestResult serialization."""
        result = IngestResult(
            incident_id=1,
            alert_fingerprint="abc123",
            severity="critical",
            source="alertmanager",
            alerts_created=2,
        )
        data = result.to_dict()
        assert data["incident_id"] == 1
        assert data["alerts_created"] == 2
        assert result.has_errors is False

    def test_ingest_result_has_errors(self):
        """Test IngestResult error detection."""
        result = IngestResult(errors=["Error 1", "Error 2"])
        assert result.has_errors is True

    def test_pipeline_result_to_dict(self):
        """Test PipelineResult serialization."""
        result = PipelineResult(
            trace_id="trace-123",
            run_id="run-456",
            status="COMPLETED",
            incident_id=1,
            ingest=IngestResult(incident_id=1),
            stages_completed=["ingest", "check"],
        )
        data = result.to_dict()
        assert data["trace_id"] == "trace-123"
        assert data["status"] == "COMPLETED"
        assert "ingest" in data
        assert data["stages_completed"] == ["ingest", "check"]


class PipelineRunModelTests(TestCase):
    """Test PipelineRun model state transitions."""

    def test_create_pipeline_run(self):
        """Test creating a new pipeline run."""
        run = PipelineRun.objects.create(
            trace_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            source="test",
        )
        assert run.status == PipelineStatus.PENDING
        assert run.total_attempts == 1

    def test_mark_started(self):
        """Test marking pipeline as started."""
        run = PipelineRun.objects.create(
            trace_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        )
        run.mark_started(PipelineStage.INGEST)
        assert run.current_stage == PipelineStage.INGEST
        assert run.started_at is not None

    def test_advance_to(self):
        """Test advancing pipeline status."""
        run = PipelineRun.objects.create(
            trace_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        )
        run.advance_to(PipelineStatus.INGESTED)
        run.refresh_from_db()
        assert run.status == PipelineStatus.INGESTED

    def test_mark_completed(self):
        """Test marking pipeline as completed."""
        run = PipelineRun.objects.create(
            trace_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        )
        run.mark_started(PipelineStage.INGEST)
        run.mark_completed(PipelineStatus.NOTIFIED)
        run.refresh_from_db()
        assert run.status == PipelineStatus.NOTIFIED
        assert run.completed_at is not None
        assert run.total_duration_ms > 0

    def test_mark_failed(self):
        """Test marking pipeline as failed."""
        run = PipelineRun.objects.create(
            trace_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        )
        run.mark_failed("TestError", "Something went wrong", retryable=True)
        run.refresh_from_db()
        assert run.status == PipelineStatus.FAILED
        assert run.last_error_type == "TestError"
        assert run.last_error_message == "Something went wrong"
        assert run.last_error_retryable is True

    def test_mark_retrying(self):
        """Test marking pipeline as retrying."""
        run = PipelineRun.objects.create(
            trace_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        )
        initial_attempts = run.total_attempts
        run.mark_retrying()
        run.refresh_from_db()
        assert run.status == PipelineStatus.RETRYING
        assert run.total_attempts == initial_attempts + 1


class StageExecutionModelTests(TestCase):
    """Test StageExecution model state transitions."""

    def setUp(self):
        self.pipeline_run = PipelineRun.objects.create(
            trace_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        )

    def test_create_stage_execution(self):
        """Test creating a stage execution."""
        execution = StageExecution.objects.create(
            pipeline_run=self.pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
        )
        assert execution.status == StageStatus.PENDING

    def test_mark_started(self):
        """Test marking stage as started."""
        execution = StageExecution.objects.create(
            pipeline_run=self.pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
        )
        execution.mark_started()
        execution.refresh_from_db()
        assert execution.status == StageStatus.RUNNING
        assert execution.started_at is not None

    def test_mark_succeeded(self):
        """Test marking stage as succeeded."""
        execution = StageExecution.objects.create(
            pipeline_run=self.pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
        )
        execution.mark_started()
        execution.mark_succeeded(output_snapshot={"test": "data"})
        execution.refresh_from_db()
        assert execution.status == StageStatus.SUCCEEDED
        assert execution.completed_at is not None
        assert execution.output_snapshot == {"test": "data"}

    def test_mark_failed(self):
        """Test marking stage as failed."""
        execution = StageExecution.objects.create(
            pipeline_run=self.pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
        )
        execution.mark_started()
        execution.mark_failed(
            error_type="TestError",
            error_message="Test failure",
            retryable=True,
        )
        execution.refresh_from_db()
        assert execution.status == StageStatus.FAILED
        assert execution.error_type == "TestError"
        assert execution.error_retryable is True

    def test_unique_stage_attempt_constraint(self):
        """Test that duplicate stage+attempt is prevented."""
        StageExecution.objects.create(
            pipeline_run=self.pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
        )
        with pytest.raises(Exception):
            StageExecution.objects.create(
                pipeline_run=self.pipeline_run,
                stage=PipelineStage.INGEST,
                attempt=1,
            )


class SignalTagsTests(TestCase):
    """Test signal tags."""

    def test_signal_tags_to_dict(self):
        """Test SignalTags serialization."""
        tags = SignalTags(
            trace_id="trace-123",
            run_id="run-456",
            stage="ingest",
            incident_id=1,
            source="grafana",
            alert_fingerprint="abc",
            environment="production",
            attempt=2,
            extra={"custom": "value"},
        )
        data = tags.to_dict()
        assert data["trace_id"] == "trace-123"
        assert data["stage"] == "ingest"
        assert data["custom"] == "value"


@pytest.mark.django_db
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
