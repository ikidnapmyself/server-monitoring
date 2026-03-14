"""Tests for PipelineOrchestrator."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
)
from apps.orchestration.models import (
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
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


class ResumePipelineTests(TestCase):
    """Tests for resume_pipeline method."""

    def test_resume_pipeline_run_not_found(self):
        """resume_pipeline raises ValueError when run_id does not exist."""
        orchestrator = PipelineOrchestrator()
        with self.assertRaises(ValueError, msg="Pipeline run not found"):
            orchestrator.resume_pipeline(run_id="nonexistent-run-id", payload={})

    def test_resume_pipeline_wrong_status(self):
        """resume_pipeline raises ValueError when status is not FAILED or RETRYING."""
        orchestrator = PipelineOrchestrator()
        pipeline_run = orchestrator.start_pipeline(payload={}, source="test")
        # Status is PENDING, which is not resumable
        with self.assertRaises(ValueError, msg="Pipeline cannot be resumed"):
            orchestrator.resume_pipeline(run_id=pipeline_run.run_id, payload={})

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_resume_pipeline_success(self, mock_execute):
        """resume_pipeline marks run as retrying and executes pipeline."""
        orchestrator = PipelineOrchestrator()
        pipeline_run = orchestrator.start_pipeline(payload={}, source="test")
        # Manually set status to FAILED so it can be resumed
        pipeline_run.status = PipelineStatus.FAILED
        pipeline_run.save(update_fields=["status"])

        mock_execute.side_effect = [
            IngestResult(incident_id=None),
            CheckResult(checks_run=1),
            AnalyzeResult(summary="ok"),
            NotifyResult(channels_succeeded=1),
        ]

        result = orchestrator.resume_pipeline(run_id=pipeline_run.run_id, payload={"payload": {}})

        # Verify mark_retrying was called (total_attempts incremented)
        pipeline_run.refresh_from_db()
        assert pipeline_run.total_attempts == 2
        assert result.status == "COMPLETED"


class SkipCompletedStagesTests(TestCase):
    """Tests for skipping already-completed stages on resume."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_resume_skips_completed_ingest_stage(self, mock_execute):
        """When resuming, completed INGEST stage is skipped and incident_id extracted."""
        orchestrator = PipelineOrchestrator()
        pipeline_run = orchestrator.start_pipeline(payload={}, source="test")

        # Simulate INGEST already completed successfully with incident_id
        StageExecution.objects.create(
            pipeline_run=pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"incident_id": 42, "severity": "critical"},
        )

        # Set status to FAILED so we can resume
        pipeline_run.status = PipelineStatus.FAILED
        pipeline_run.save(update_fields=["status"])

        # Only CHECK, ANALYZE, NOTIFY should be executed (3 calls)
        mock_execute.side_effect = [
            CheckResult(checks_run=1),
            AnalyzeResult(summary="ok"),
            NotifyResult(channels_succeeded=1),
        ]

        result = orchestrator.resume_pipeline(run_id=pipeline_run.run_id, payload={"payload": {}})

        assert result.status == "COMPLETED"
        assert mock_execute.call_count == 3

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_resume_skips_completed_non_ingest_stage(self, mock_execute):
        """When resuming, completed non-INGEST stage is skipped (no incident_id extraction)."""
        orchestrator = PipelineOrchestrator()
        pipeline_run = orchestrator.start_pipeline(payload={}, source="test")

        # Simulate both INGEST and CHECK already completed
        StageExecution.objects.create(
            pipeline_run=pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"incident_id": 42},
        )
        StageExecution.objects.create(
            pipeline_run=pipeline_run,
            stage=PipelineStage.CHECK,
            attempt=1,
            status=StageStatus.SUCCEEDED,
            output_snapshot={"checks_run": 5},
        )

        pipeline_run.status = PipelineStatus.FAILED
        pipeline_run.save(update_fields=["status"])

        # Only ANALYZE and NOTIFY should be executed
        mock_execute.side_effect = [
            AnalyzeResult(summary="ok"),
            NotifyResult(channels_succeeded=1),
        ]

        result = orchestrator.resume_pipeline(run_id=pipeline_run.run_id, payload={"payload": {}})

        assert result.status == "COMPLETED"
        assert mock_execute.call_count == 2

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_resume_skips_completed_stage_without_output_snapshot(self, mock_execute):
        """Completed stage with no output_snapshot is still skipped."""
        orchestrator = PipelineOrchestrator()
        pipeline_run = orchestrator.start_pipeline(payload={}, source="test")

        # Completed stage with empty output_snapshot
        StageExecution.objects.create(
            pipeline_run=pipeline_run,
            stage=PipelineStage.INGEST,
            attempt=1,
            status=StageStatus.SUCCEEDED,
            output_snapshot={},
        )

        pipeline_run.status = PipelineStatus.FAILED
        pipeline_run.save(update_fields=["status"])

        mock_execute.side_effect = [
            CheckResult(checks_run=1),
            AnalyzeResult(summary="ok"),
            NotifyResult(channels_succeeded=1),
        ]

        result = orchestrator.resume_pipeline(run_id=pipeline_run.run_id, payload={"payload": {}})

        assert result.status == "COMPLETED"
        assert mock_execute.call_count == 3


class AnalyzeFallbackContinuesTests(TestCase):
    """Tests for ANALYZE stage with fallback_used continuing pipeline."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_analyze_with_errors_and_fallback_continues(self, mock_execute):
        """When analyze has errors but fallback_used=True, pipeline continues."""
        mock_execute.side_effect = [
            IngestResult(incident_id=None),
            CheckResult(checks_run=1),
            AnalyzeResult(
                summary="Fallback summary",
                fallback_used=True,
                errors=["AI provider unavailable"],
            ),
            NotifyResult(channels_succeeded=1),
        ]

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "COMPLETED"
        assert result.analyze.fallback_used is True
        assert result.analyze.has_errors is True


class StageErrorInExecutePipelineTests(TestCase):
    """Tests for error handling within _execute_pipeline (not _execute_stage_with_retry)."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_non_analyze_stage_with_errors_raises(self, mock_execute):
        """When _execute_stage_with_retry returns a non-analyze result with errors,
        _execute_pipeline raises StageExecutionError (line 342)."""
        # Return an IngestResult that has errors - this triggers the has_errors
        # check in _execute_pipeline (lines 335-346)
        mock_execute.side_effect = [
            IngestResult(incident_id=None, errors=["Ingest failed"]),
        ]

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "FAILED"
        assert result.final_error is not None
        assert "Ingest failed" in result.final_error.message

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_check_stage_with_errors_raises(self, mock_execute):
        """CheckResult with errors triggers StageExecutionError in _execute_pipeline."""
        mock_execute.side_effect = [
            IngestResult(incident_id=None),
            CheckResult(checks_run=1, errors=["Check failed"]),
        ]

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "FAILED"
        assert "Check failed" in result.final_error.message


class GenericExceptionHandlerTests(TestCase):
    """Tests for generic (non-StageExecutionError) exception in _execute_pipeline."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_generic_exception_caught_and_pipeline_fails(self, mock_execute):
        """Non-StageExecutionError is caught and pipeline marked FAILED with stack_trace."""
        mock_execute.side_effect = RuntimeError("Something unexpected broke")

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "FAILED"
        assert result.final_error is not None
        assert result.final_error.error_type == "RuntimeError"
        assert "Something unexpected broke" in result.final_error.message
        assert result.final_error.stack_trace is not None
        assert result.final_error.retryable is True


class StageRetryWithBackoffTests(TestCase):
    """Tests for stage retry + backoff logic."""

    @patch("apps.orchestration.orchestrator.time.sleep")
    def test_stage_execution_error_retry_then_succeed(self, mock_sleep):
        """StageExecutionError with retryable=True retries and succeeds on second attempt."""
        orchestrator = PipelineOrchestrator(max_retries=2, backoff_factor=2.0)

        # First call raises retryable error, second succeeds
        ingest_result = IngestResult(incident_id=None)
        executor_mock = MagicMock()
        executor_mock.execute.side_effect = [
            StageExecutionError(
                stage=PipelineStage.INGEST,
                errors=["Transient error"],
                retryable=True,
            ),
            ingest_result,
        ]
        orchestrator.executors[PipelineStage.INGEST] = executor_mock

        # Mock other stages to return immediately
        for stage in [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]:
            mock_exec = MagicMock()
            if stage == PipelineStage.CHECK:
                mock_exec.execute.return_value = CheckResult(checks_run=1)
            elif stage == PipelineStage.ANALYZE:
                mock_exec.execute.return_value = AnalyzeResult(summary="ok")
            else:
                mock_exec.execute.return_value = NotifyResult(channels_succeeded=1)
            orchestrator.executors[stage] = mock_exec

        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "COMPLETED"
        # Verify backoff: 2.0^1 = 2.0
        mock_sleep.assert_called_once_with(2.0)
        assert executor_mock.execute.call_count == 2

    @patch("apps.orchestration.orchestrator.time.sleep")
    def test_generic_exception_retry_then_succeed(self, mock_sleep):
        """Generic RuntimeError retries and succeeds on second attempt."""
        orchestrator = PipelineOrchestrator(max_retries=2, backoff_factor=2.0)

        # First call raises generic error, second succeeds
        ingest_result = IngestResult(incident_id=None)
        executor_mock = MagicMock()
        executor_mock.execute.side_effect = [
            RuntimeError("Transient failure"),
            ingest_result,
        ]
        orchestrator.executors[PipelineStage.INGEST] = executor_mock

        for stage in [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]:
            mock_exec = MagicMock()
            if stage == PipelineStage.CHECK:
                mock_exec.execute.return_value = CheckResult(checks_run=1)
            elif stage == PipelineStage.ANALYZE:
                mock_exec.execute.return_value = AnalyzeResult(summary="ok")
            else:
                mock_exec.execute.return_value = NotifyResult(channels_succeeded=1)
            orchestrator.executors[stage] = mock_exec

        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "COMPLETED"
        mock_sleep.assert_called_once_with(2.0)
        assert executor_mock.execute.call_count == 2

    @patch("apps.orchestration.orchestrator.time.sleep")
    def test_stage_execution_error_exhausts_retries(self, mock_sleep):
        """StageExecutionError exhausts all retries and propagates."""
        orchestrator = PipelineOrchestrator(max_retries=2, backoff_factor=2.0)

        executor_mock = MagicMock()
        executor_mock.execute.side_effect = StageExecutionError(
            stage=PipelineStage.INGEST,
            errors=["Persistent error"],
            retryable=True,
        )
        orchestrator.executors[PipelineStage.INGEST] = executor_mock

        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "FAILED"
        assert "Persistent error" in result.final_error.message
        # Should have retried once (attempt 1 fails, backoff, attempt 2 fails, raise)
        assert executor_mock.execute.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

    @patch("apps.orchestration.orchestrator.time.sleep")
    def test_generic_exception_exhausts_retries(self, mock_sleep):
        """Generic exception exhausts all retries and propagates."""
        orchestrator = PipelineOrchestrator(max_retries=2, backoff_factor=2.0)

        executor_mock = MagicMock()
        executor_mock.execute.side_effect = RuntimeError("Always fails")
        orchestrator.executors[PipelineStage.INGEST] = executor_mock

        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "FAILED"
        assert result.final_error.error_type == "RuntimeError"
        assert executor_mock.execute.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

    @patch("apps.orchestration.orchestrator.time.sleep")
    def test_non_retryable_stage_error_does_not_retry(self, mock_sleep):
        """StageExecutionError with retryable=False does not retry."""
        orchestrator = PipelineOrchestrator(max_retries=3, backoff_factor=2.0)

        executor_mock = MagicMock()
        executor_mock.execute.side_effect = StageExecutionError(
            stage=PipelineStage.INGEST,
            errors=["Fatal error"],
            retryable=False,
        )
        orchestrator.executors[PipelineStage.INGEST] = executor_mock

        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "FAILED"
        # Should not retry at all - just the first attempt
        assert executor_mock.execute.call_count == 1
        mock_sleep.assert_not_called()

    @patch("apps.orchestration.orchestrator.time.sleep")
    def test_executor_returns_result_with_errors_triggers_retry(self, mock_sleep):
        """Executor returns a result with has_errors=True, triggers StageExecutionError retry."""
        orchestrator = PipelineOrchestrator(max_retries=2, backoff_factor=2.0)

        error_result = IngestResult(incident_id=None, errors=["Something went wrong"])
        success_result = IngestResult(incident_id=None)

        executor_mock = MagicMock()
        executor_mock.execute.side_effect = [error_result, success_result]
        orchestrator.executors[PipelineStage.INGEST] = executor_mock

        for stage in [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]:
            mock_exec = MagicMock()
            if stage == PipelineStage.CHECK:
                mock_exec.execute.return_value = CheckResult(checks_run=1)
            elif stage == PipelineStage.ANALYZE:
                mock_exec.execute.return_value = AnalyzeResult(summary="ok")
            else:
                mock_exec.execute.return_value = NotifyResult(channels_succeeded=1)
            orchestrator.executors[stage] = mock_exec

        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "COMPLETED"
        assert executor_mock.execute.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

    @patch("apps.orchestration.orchestrator.time.sleep")
    def test_analyze_fallback_result_does_not_trigger_retry(self, mock_sleep):
        """AnalyzeResult with errors but fallback_used=True does not trigger retry."""
        orchestrator = PipelineOrchestrator(max_retries=2, backoff_factor=2.0)

        # Set up INGEST and CHECK to succeed normally
        ingest_mock = MagicMock()
        ingest_mock.execute.return_value = IngestResult(incident_id=None)
        orchestrator.executors[PipelineStage.INGEST] = ingest_mock

        check_mock = MagicMock()
        check_mock.execute.return_value = CheckResult(checks_run=1)
        orchestrator.executors[PipelineStage.CHECK] = check_mock

        # ANALYZE returns errors with fallback_used=True - should NOT retry
        analyze_mock = MagicMock()
        analyze_mock.execute.return_value = AnalyzeResult(
            summary="Fallback",
            fallback_used=True,
            errors=["AI unavailable"],
        )
        orchestrator.executors[PipelineStage.ANALYZE] = analyze_mock

        notify_mock = MagicMock()
        notify_mock.execute.return_value = NotifyResult(channels_succeeded=1)
        orchestrator.executors[PipelineStage.NOTIFY] = notify_mock

        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "COMPLETED"
        # ANALYZE should only be called once (no retry)
        assert analyze_mock.execute.call_count == 1


class SafetyNetTests(TestCase):
    """Tests for the safety net at end of _execute_stage_with_retry (lines 547-550)."""

    def test_safety_net_runtime_error_with_zero_retries(self):
        """With max_retries=0, the retry loop never executes, hitting the safety net."""
        orchestrator = PipelineOrchestrator(max_retries=0, backoff_factor=1.0)

        # The for loop range(1, 0+1) = range(1, 1) is empty, so the loop body
        # never runs. last_error stays None, hitting line 550:
        # raise RuntimeError("Stage execution failed without error")
        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "FAILED"
        assert result.final_error is not None
        assert result.final_error.error_type == "RuntimeError"
        assert "Stage execution failed without error" in result.final_error.message


class ChecksOnlyTests(TestCase):
    """Tests for checks_only mode that skips ingest/analyze/notify stages."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_checks_only_runs_only_check_stage(self, mock_execute):
        """When checks_only=True, only the CHECK stage is executed."""
        mock_execute.return_value = CheckResult(checks_run=3)

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(
            payload={"checks_only": True},
            source="test",
        )

        assert result.status == "COMPLETED"
        assert mock_execute.call_count == 1
        called_stage = mock_execute.call_args[1]["stage"]
        assert called_stage == PipelineStage.CHECK
        assert len(result.stages_completed) == 1
        assert PipelineStage.CHECK in result.stages_completed
        assert PipelineStage.INGEST not in result.stages_completed
        assert PipelineStage.NOTIFY not in result.stages_completed

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_checks_only_pipeline_marked_checked(self, mock_execute):
        """checks_only run completes with CHECKED status, not NOTIFIED."""
        mock_execute.return_value = CheckResult(checks_run=1)

        orchestrator = PipelineOrchestrator()
        orchestrator.run_pipeline(payload={"checks_only": True}, source="test")

        from apps.orchestration.models import PipelineRun

        run = PipelineRun.objects.order_by("-started_at").first()
        assert run is not None
        assert run.status == PipelineStatus.CHECKED

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_normal_pipeline_still_runs_all_stages(self, mock_execute):
        """Without checks_only, all 4 stages run (regression guard)."""
        mock_execute.side_effect = [
            IngestResult(incident_id=None, alerts_created=1),
            CheckResult(checks_run=2),
            AnalyzeResult(summary="ok"),
            NotifyResult(channels_succeeded=1),
        ]

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(
            payload={"payload": {}},
            source="test",
        )

        assert result.status == "COMPLETED"
        assert mock_execute.call_count == 4
        assert len(result.stages_completed) == 4
