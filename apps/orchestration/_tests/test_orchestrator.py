"""Tests for PipelineOrchestrator."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Node
from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
)
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineOrigin,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
from apps.orchestration.orchestrator import PipelineOrchestrator, StageExecutionError


def make_subject_alert(fingerprint="fp-subject"):
    """A real Alert row for a run to route on.

    Downstream stages come from the lane matched against the run's *subject alert*
    (``IngestResult.alert_id``), so a flow test that expects CHECK/ANALYZE/NOTIFY
    to run needs an actual alert — an ingest that produced none has nothing to
    route and legitimately stops after INGEST.

    Named ``make_`` to stay distinct from ``routing.subject_alert``, which picks
    the subject out of a batch rather than creating one.
    """
    return Alert.objects.create(
        fingerprint=fingerprint,
        source="test",
        name="cpu",
        severity="critical",
        started_at=timezone.now(),
        labels={},
    )


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

    def test_start_pipeline_persists_payload_for_drain(self):
        """start_pipeline stores the raw payload so a drain can process the run later."""
        orchestrator = PipelineOrchestrator()
        payload = {"driver": "generic", "payload": {"k": "v"}}
        run = orchestrator.start_pipeline(payload, source="generic")

        run.refresh_from_db()
        assert run.status == PipelineStatus.PENDING
        assert run.inbound_payload == {"driver": "generic", "payload": {"k": "v"}}

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
        alert = make_subject_alert()
        mock_execute.side_effect = [
            IngestResult(alert_id=alert.id, incident_id=None, alerts_created=1),
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
        alert = make_subject_alert()
        mock_execute.side_effect = [
            IngestResult(alert_id=alert.id, incident_id=None),
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
            output_snapshot={
                "alert_id": make_subject_alert().id,
                "incident_id": 42,
                "severity": "critical",
            },
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
            output_snapshot={"alert_id": make_subject_alert().id, "incident_id": 42},
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
        """Completed INGEST with no output_snapshot is skipped and stops the run.

        No snapshot means no subject alert, and routing needs one — so there is
        nothing downstream to run. The stage is still skipped rather than re-executed.
        """
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

        result = orchestrator.resume_pipeline(run_id=pipeline_run.run_id, payload={"payload": {}})

        assert result.status == "COMPLETED"
        assert result.stages_completed == []
        assert mock_execute.call_count == 0
        assert StageExecution.objects.filter(pipeline_run=pipeline_run).count() == 1


class AnalyzeFallbackContinuesTests(TestCase):
    """Tests for ANALYZE stage with fallback_used continuing pipeline."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_analyze_with_errors_and_fallback_continues(self, mock_execute):
        """When analyze has errors but fallback_used=True, pipeline continues."""
        alert = make_subject_alert()
        mock_execute.side_effect = [
            IngestResult(alert_id=alert.id, incident_id=None),
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
        alert = make_subject_alert()
        mock_execute.side_effect = [
            IngestResult(alert_id=alert.id, incident_id=None),
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
        ingest_mock.execute.return_value = IngestResult(alert_id=make_subject_alert().id)
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
        alert = make_subject_alert()
        mock_execute.side_effect = [
            IngestResult(alert_id=alert.id, incident_id=None, alerts_created=1),
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
        assert PipelineStage.CHECK in result.stages_completed


class CheckOmittingLaneTests(TestCase):
    """A lane that lists no CHECK runs INGEST -> ANALYZE -> NOTIFY and ends NOTIFIED.

    Was ``SkipCheckersTests``, which drove the same two outcomes through a
    ``payload["skip_checkers"]`` flag. The flag is gone; the outcomes are not, so
    they are re-driven through the only thing that selects stages now — the
    matched lane's ``stages`` list. Terminal status still comes from the *last*
    stage that ran, which is what makes a three-stage lane worth asserting
    separately from the full four.
    """

    def _lane_without_check(self):
        PipelineDefinition.objects.create(
            name="analyze-notify", match=[], priority=1, stages=["analyze", "notify"]
        )

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_lane_without_check_omits_check_but_reaches_notify(self, mock_execute):
        self._lane_without_check()
        alert = make_subject_alert()
        mock_execute.side_effect = [
            IngestResult(alert_id=alert.id, incident_id=None, alerts_created=1),
            AnalyzeResult(summary="ok"),
            NotifyResult(channels_succeeded=1),
        ]

        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        assert result.status == "COMPLETED"
        assert mock_execute.call_count == 3
        assert PipelineStage.CHECK not in result.stages_completed
        assert PipelineStage.INGEST in result.stages_completed
        assert PipelineStage.ANALYZE in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_lane_without_check_is_marked_notified(self, mock_execute):
        self._lane_without_check()
        alert = make_subject_alert()
        mock_execute.side_effect = [
            IngestResult(alert_id=alert.id, incident_id=None, alerts_created=1),
            AnalyzeResult(summary="ok"),
            NotifyResult(channels_succeeded=1),
        ]

        orchestrator = PipelineOrchestrator()
        orchestrator.run_pipeline(payload={"payload": {}}, source="test")

        from apps.orchestration.models import PipelineRun

        run = PipelineRun.objects.order_by("-started_at").first()
        assert run is not None
        assert run.status == PipelineStatus.NOTIFIED


@pytest.mark.django_db
def test_checker_generated_run_has_null_node():
    run = PipelineOrchestrator().start_pipeline(
        payload={"checks_only": True},
        source="cli",
        origin=PipelineOrigin.CHECKER_GENERATED,
    )
    assert run.origin == PipelineOrigin.CHECKER_GENERATED
    assert run.node is None


@pytest.mark.django_db
def test_incoming_run_resolves_node_from_instance_id():
    Node.objects.create(instance_id="agent-9")  # node must exist to resolve
    payload = {"payload": {"alerts": [{"labels": {"instance_id": "agent-9"}}]}}
    run = PipelineOrchestrator().start_pipeline(
        payload=payload, source="grafana", origin=PipelineOrigin.INCOMING_WEBHOOK
    )
    assert run.origin == PipelineOrigin.INCOMING_WEBHOOK
    assert run.node is not None and run.node.instance_id == "agent-9"


@pytest.mark.django_db
def test_incoming_run_without_instance_id_has_null_node():
    run = PipelineOrchestrator().start_pipeline(payload={}, source="grafana")
    assert run.node is None


@pytest.mark.django_db
def test_incoming_run_resolves_node_from_cluster_top_level_instance_id():
    """Cluster shape: instance_id lives at the top of the inner payload."""
    Node.objects.create(instance_id="web-03")
    payload = {"payload": {"instance_id": "web-03", "alerts": []}}
    run = PipelineOrchestrator().start_pipeline(payload=payload, source="cluster")
    assert run.node is not None and run.node.instance_id == "web-03"


@pytest.mark.django_db
def test_incoming_run_resolves_node_from_instance_label_fallthrough():
    """Falls through instance_id -> instance -> hostname in the first alert labels."""
    Node.objects.create(instance_id="host-7")
    payload = {"payload": {"alerts": [{"labels": {"hostname": "host-7"}}]}}
    run = PipelineOrchestrator().start_pipeline(payload=payload, source="datadog")
    assert run.node is not None and run.node.instance_id == "host-7"


@pytest.mark.django_db
def test_incoming_run_with_empty_alerts_has_null_node():
    """No instance_id anywhere (empty alerts list) leaves node NULL."""
    payload = {"payload": {"alerts": []}}
    run = PipelineOrchestrator().start_pipeline(payload=payload, source="grafana")
    assert run.node is None


@pytest.mark.django_db
def test_incoming_run_with_labelless_alert_has_null_node():
    """An alert with no usable instance label leaves node NULL."""
    payload = {"payload": {"alerts": [{"labels": {"foo": "bar"}}]}}
    run = PipelineOrchestrator().start_pipeline(payload=payload, source="grafana")
    assert run.node is None


@pytest.mark.django_db
def test_incoming_run_with_non_dict_labels_has_null_node():
    """Malformed webhook input (labels is a string) must not raise; node stays NULL."""
    payload = {"payload": {"alerts": [{"labels": "pwned"}]}}
    run = PipelineOrchestrator().start_pipeline(payload=payload, source="grafana")
    assert run.node is None


@pytest.mark.django_db
def test_incoming_run_with_non_string_instance_id_has_null_node():
    """A non-str top-level instance_id must not reach the ORM filter; node stays NULL."""
    payload = {"payload": {"instance_id": ["not", "a", "string"]}}
    run = PipelineOrchestrator().start_pipeline(payload=payload, source="cluster")
    assert run.node is None


class DownstreamRunTests(TestCase):
    """A downstream run has no entry stage: it runs exactly its lane.

    Its incident was ingested by the parent run, so re-ingesting is not merely
    wasteful, it is wrong — there is no payload to ingest. Treating ANALYZE as an
    entry stage would be worse: a resolved incident routes to a notify-only lane,
    and forcing an entry stage would call the AI on an all-clear.
    """

    def _incident_with_alert(self, severity="critical", status="firing"):
        from apps.alerts.models import Incident

        incident = Incident.objects.create(title="Disk full", severity=severity)
        Alert.objects.create(
            fingerprint=f"fp-{incident.id}",
            source="cluster",
            name="disk",
            severity=severity,
            status=status,
            started_at=timezone.now(),
            labels={},
            incident=incident,
        )
        return incident

    def _downstream_run(self, incident, run_id="r-1"):
        from apps.orchestration.models import PipelineRun

        return PipelineRun.objects.create(
            trace_id=f"t-{run_id}",
            run_id=run_id,
            source="cluster",
            origin=PipelineOrigin.INCOMING_WEBHOOK,
            status=PipelineStatus.PENDING,
            inbound_payload={"downstream_incident_id": incident.id},
        )

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_downstream_run_executes_only_its_lane_stages(self, mock_execute):
        mock_execute.return_value = NotifyResult(channels_succeeded=1)
        incident = self._incident_with_alert()
        PipelineDefinition.objects.create(
            name="lane-notify-only", match=[], stages=["notify"], priority=10, is_active=True
        )
        run = self._downstream_run(incident)

        result = PipelineOrchestrator().execute_run(run)

        assert result.status == "COMPLETED"
        assert result.stages_completed == [PipelineStage.NOTIFY]
        assert mock_execute.call_count == 1
        assert mock_execute.call_args[1]["stage"] == PipelineStage.NOTIFY

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_downstream_run_carries_its_incident_into_every_stage(self, mock_execute):
        """notify reads incident_id to find the lane's channel; nothing re-derives it."""
        mock_execute.return_value = NotifyResult(channels_succeeded=1)
        incident = self._incident_with_alert()
        PipelineDefinition.objects.create(
            name="lane-notify-only", match=[], stages=["notify"], priority=10, is_active=True
        )
        run = self._downstream_run(incident)

        result = PipelineOrchestrator().execute_run(run)

        assert mock_execute.call_args[1]["incident_id"] == incident.id
        assert result.incident_id == incident.id
        run.refresh_from_db()
        assert run.incident_id == incident.id

    def test_downstream_run_with_no_matching_lane_fails_no_route(self):
        incident = self._incident_with_alert()
        PipelineDefinition.objects.all().delete()
        run = self._downstream_run(incident, run_id="r-2")

        PipelineOrchestrator().execute_run(run)

        run.refresh_from_db()
        assert run.status == PipelineStatus.FAILED
        assert "no_route" in run.last_error_message
        assert run.last_error_retryable is False

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_downstream_run_never_ingests(self, mock_execute):
        """The parent already ingested; there is no payload here to ingest."""
        mock_execute.return_value = AnalyzeResult(summary="ok")
        incident = self._incident_with_alert()
        PipelineDefinition.objects.create(
            name="analyze-only", match=[], stages=["analyze"], priority=10, is_active=True
        )
        run = self._downstream_run(incident, run_id="r-3")

        result = PipelineOrchestrator().execute_run(run)

        assert result.status == "COMPLETED"
        assert PipelineStage.INGEST not in result.stages_completed
        assert result.stages_completed == [PipelineStage.ANALYZE]
        run.refresh_from_db()
        assert run.status == PipelineStatus.ANALYZED

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator._execute_stage_with_retry")
    def test_downstream_lane_with_no_stages_completes_cleanly(self, mock_execute):
        """An empty stage list is legal: nothing to run is not a failure."""
        incident = self._incident_with_alert()
        PipelineDefinition.objects.create(
            name="lane-empty", match=[], stages=[], priority=10, is_active=True
        )
        run = self._downstream_run(incident, run_id="r-4")

        result = PipelineOrchestrator().execute_run(run)

        assert result.status == "COMPLETED"
        assert result.stages_completed == []
        assert mock_execute.call_count == 0
        run.refresh_from_db()
        assert run.status == PipelineStatus.INGESTED
