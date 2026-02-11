"""Tests for PipelineRun and StageExecution model state transitions."""

import uuid

from django.test import TestCase

from apps.orchestration.models import (
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)


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
        with self.assertRaises(Exception):
            StageExecution.objects.create(
                pipeline_run=self.pipeline_run,
                stage=PipelineStage.INGEST,
                attempt=1,
            )
