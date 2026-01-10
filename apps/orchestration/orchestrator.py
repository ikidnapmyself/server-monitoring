"""
Pipeline Orchestrator service.

The main entry point for pipeline orchestration. Controls the full lifecycle
of an incident through: alerts → checkers → intelligence → notify.

Key responsibilities:
1. State machine: PENDING → INGESTED → CHECKED → ANALYZED → NOTIFIED
2. Correlation IDs: trace_id attached to all logs/events/records
3. Contracts: Each stage returns structured DTOs
4. Observability: Signals at every stage boundary
5. Failure policy: Stage-local retries with backoff, intelligence fallback
"""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from typing import Any

from django.conf import settings
from django.db import transaction

from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
    PipelineResult,
    StageContext,
    StageError,
)
from apps.orchestration.executors import (
    AnalyzeExecutor,
    BaseExecutor,
    CheckExecutor,
    IngestExecutor,
    NotifyExecutor,
)
from apps.orchestration.models import (
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
from apps.orchestration.signals import (
    SignalTags,
    emit_pipeline_completed,
    emit_pipeline_started,
    emit_stage_failed,
    emit_stage_retrying,
    emit_stage_started,
    emit_stage_succeeded,
)

logger = logging.getLogger(__name__)


# Stage order for the pipeline
STAGE_ORDER = [
    PipelineStage.INGEST,
    PipelineStage.CHECK,
    PipelineStage.ANALYZE,
    PipelineStage.NOTIFY,
]

# Mapping stage to next status after completion
STAGE_TO_STATUS = {
    PipelineStage.INGEST: PipelineStatus.INGESTED,
    PipelineStage.CHECK: PipelineStatus.CHECKED,
    PipelineStage.ANALYZE: PipelineStatus.ANALYZED,
    PipelineStage.NOTIFY: PipelineStatus.NOTIFIED,
}


class PipelineOrchestrator:
    """
    Main orchestrator service for pipeline execution.

    Usage:
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_pipeline(payload, source="grafana")
    """

    max_retries: int
    backoff_factor: float
    intelligence_fallback: bool
    executors: dict[PipelineStage, BaseExecutor]

    def __init__(
        self,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        intelligence_fallback: bool | None = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            max_retries: Max retries per stage (default from settings).
            backoff_factor: Backoff multiplier for retries (default from settings).
            intelligence_fallback: Enable fallback when AI fails (default from settings).
        """
        self.max_retries = (
            max_retries
            if max_retries is not None
            else int(getattr(settings, "ORCHESTRATION_MAX_RETRIES_PER_STAGE", 3))
        )
        self.backoff_factor = (
            backoff_factor
            if backoff_factor is not None
            else float(getattr(settings, "ORCHESTRATION_BACKOFF_FACTOR", 2.0))
        )
        self.intelligence_fallback = (
            intelligence_fallback
            if intelligence_fallback is not None
            else bool(getattr(settings, "ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED", True))
        )

        # Initialize executors
        self.executors = {
            PipelineStage.INGEST: IngestExecutor(),
            PipelineStage.CHECK: CheckExecutor(),
            PipelineStage.ANALYZE: AnalyzeExecutor(fallback_enabled=self.intelligence_fallback),
            PipelineStage.NOTIFY: NotifyExecutor(),
        }

    def start_pipeline(
        self,
        payload: dict[str, Any],
        source: str = "unknown",
        trace_id: str | None = None,
        environment: str = "production",
    ) -> PipelineRun:
        """
        Start a new pipeline run.

        Creates the PipelineRun record with correlation IDs.

        Args:
            payload: Raw payload to process.
            source: Source system (grafana, alertmanager, etc.).
            trace_id: Optional trace ID (generated if not provided).
            environment: Environment name.

        Returns:
            Created PipelineRun instance.
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())

        run_id = str(uuid.uuid4())

        with transaction.atomic():
            pipeline_run = PipelineRun.objects.create(
                trace_id=trace_id,
                run_id=run_id,
                source=source,
                environment=environment,
                status=PipelineStatus.PENDING,
                max_retries=self.max_retries,
            )

        logger.info(
            f"Pipeline started: trace_id={trace_id}, run_id={run_id}",
            extra={"trace_id": trace_id, "run_id": run_id, "source": source},
        )

        return pipeline_run

    def run_pipeline(
        self,
        payload: dict[str, Any],
        source: str = "unknown",
        trace_id: str | None = None,
        environment: str = "production",
    ) -> PipelineResult:
        """
        Run the complete pipeline synchronously.

        This is the main entry point for pipeline execution.
        Executes all stages in order: ingest → check → analyze → notify.

        Args:
            payload: Raw payload to process.
            source: Source system.
            trace_id: Optional trace ID.
            environment: Environment name.

        Returns:
            PipelineResult with all stage results.
        """
        pipeline_run = self.start_pipeline(
            payload=payload,
            source=source,
            trace_id=trace_id,
            environment=environment,
        )

        return self._execute_pipeline(pipeline_run, payload)

    def resume_pipeline(self, run_id: str, payload: dict[str, Any]) -> PipelineResult:
        """
        Resume a failed/retrying pipeline from where it left off.

        Args:
            run_id: The pipeline run ID to resume.
            payload: Payload for the pipeline.

        Returns:
            PipelineResult with all stage results.
        """
        try:
            pipeline_run = PipelineRun.objects.get(run_id=run_id)
        except PipelineRun.DoesNotExist:
            raise ValueError(f"Pipeline run not found: {run_id}")

        if pipeline_run.status not in (PipelineStatus.FAILED, PipelineStatus.RETRYING):
            raise ValueError(f"Pipeline cannot be resumed from status: {pipeline_run.status}")

        pipeline_run.mark_retrying()
        return self._execute_pipeline(pipeline_run, payload)

    def _execute_pipeline(
        self,
        pipeline_run: PipelineRun,
        payload: dict[str, Any],
    ) -> PipelineResult:
        """
        Execute the pipeline stages.

        Args:
            pipeline_run: The PipelineRun instance.
            payload: Payload for the pipeline.

        Returns:
            PipelineResult with all stage results.
        """
        start_time = time.perf_counter()

        # Initialize result
        result = PipelineResult(
            trace_id=pipeline_run.trace_id,
            run_id=pipeline_run.run_id,
            status="RUNNING",
            started_at=pipeline_run.started_at,
        )

        # Build base signal tags
        base_tags = SignalTags(
            trace_id=pipeline_run.trace_id,
            run_id=pipeline_run.run_id,
            stage="pipeline",
            source=pipeline_run.source,
            environment=pipeline_run.environment,
            attempt=pipeline_run.total_attempts,
        )

        # Emit pipeline started
        emit_pipeline_started(base_tags)
        pipeline_run.mark_started(STAGE_ORDER[0])

        # Track previous stage results for context
        previous_results: dict[str, dict[str, Any]] = {}
        incident_id: int | None = None

        try:
            for stage in STAGE_ORDER:
                # Skip stages that are already completed (for resume)
                if self._stage_completed(pipeline_run, stage):
                    # Load previous result from DB
                    prev_execution = StageExecution.objects.filter(
                        pipeline_run=pipeline_run,
                        stage=stage,
                        status=StageStatus.SUCCEEDED,
                    ).first()
                    if prev_execution and prev_execution.output_snapshot:
                        previous_results[stage] = prev_execution.output_snapshot
                        if stage == PipelineStage.INGEST:
                            incident_id = prev_execution.output_snapshot.get("incident_id")
                    continue

                # Execute stage with retries
                stage_result = self._execute_stage_with_retry(
                    pipeline_run=pipeline_run,
                    stage=stage,
                    payload=payload,
                    previous_results=previous_results,
                    incident_id=incident_id,
                )

                # Store result
                stage_result_dict = stage_result.to_dict()
                previous_results[stage] = stage_result_dict

                # Update incident ID if discovered
                if stage == PipelineStage.INGEST and isinstance(stage_result, IngestResult):
                    incident_id = stage_result.incident_id
                    pipeline_run.incident_id = incident_id
                    pipeline_run.alert_fingerprint = stage_result.alert_fingerprint or ""
                    pipeline_run.normalized_payload_ref = stage_result.normalized_payload_ref or ""
                    pipeline_run.save(
                        update_fields=[
                            "incident_id",
                            "alert_fingerprint",
                            "normalized_payload_ref",
                            "updated_at",
                        ]
                    )

                # Update refs on pipeline run
                if stage == PipelineStage.CHECK and isinstance(stage_result, CheckResult):
                    pipeline_run.checker_output_ref = stage_result.checker_output_ref or ""
                    pipeline_run.save(update_fields=["checker_output_ref", "updated_at"])

                if stage == PipelineStage.ANALYZE and isinstance(stage_result, AnalyzeResult):
                    pipeline_run.intelligence_output_ref = stage_result.ai_output_ref or ""
                    pipeline_run.intelligence_fallback_used = stage_result.fallback_used
                    pipeline_run.save(
                        update_fields=[
                            "intelligence_output_ref",
                            "intelligence_fallback_used",
                            "updated_at",
                        ]
                    )

                if stage == PipelineStage.NOTIFY and isinstance(stage_result, NotifyResult):
                    pipeline_run.notify_output_ref = stage_result.notify_output_ref or ""
                    pipeline_run.save(update_fields=["notify_output_ref", "updated_at"])

                # Check for errors (non-fallback)
                if stage_result.has_errors:
                    # For analyze stage with fallback, we continue
                    if not (
                        stage == PipelineStage.ANALYZE
                        and isinstance(stage_result, AnalyzeResult)
                        and stage_result.fallback_used
                    ):
                        raise StageExecutionError(
                            stage=stage,
                            errors=stage_result.errors,
                            retryable=True,
                        )

                # Advance pipeline status
                pipeline_run.current_stage = stage
                pipeline_run.advance_to(STAGE_TO_STATUS[stage])
                result.stages_completed.append(stage)

                # Attach result to PipelineResult
                if stage == PipelineStage.INGEST and isinstance(stage_result, IngestResult):
                    result.ingest = stage_result
                elif stage == PipelineStage.CHECK and isinstance(stage_result, CheckResult):
                    result.check = stage_result
                elif stage == PipelineStage.ANALYZE and isinstance(stage_result, AnalyzeResult):
                    result.analyze = stage_result
                elif stage == PipelineStage.NOTIFY and isinstance(stage_result, NotifyResult):
                    result.notify = stage_result

            # Pipeline completed successfully
            duration_ms = (time.perf_counter() - start_time) * 1000
            result.status = "COMPLETED"
            result.incident_id = incident_id
            result.total_duration_ms = duration_ms
            pipeline_run.mark_completed(PipelineStatus.NOTIFIED)

            # Emit pipeline completed
            base_tags.incident_id = incident_id
            emit_pipeline_completed(base_tags, duration_ms, "COMPLETED")

        except StageExecutionError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            result.status = "FAILED"
            result.total_duration_ms = duration_ms
            result.final_error = StageError(
                error_type="StageExecutionError",
                message=f"Stage {e.stage} failed: {'; '.join(e.errors)}",
                retryable=e.retryable,
            )
            pipeline_run.mark_failed(
                error_type="StageExecutionError",
                message=f"Stage {e.stage} failed: {'; '.join(e.errors)}",
                retryable=e.retryable,
            )
            emit_pipeline_completed(base_tags, duration_ms, "FAILED")

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            result.status = "FAILED"
            result.total_duration_ms = duration_ms
            result.final_error = StageError(
                error_type=type(e).__name__,
                message=str(e),
                stack_trace=traceback.format_exc(),
                retryable=True,
            )
            pipeline_run.mark_failed(
                error_type=type(e).__name__,
                message=str(e),
                retryable=True,
            )
            emit_pipeline_completed(base_tags, duration_ms, "FAILED")
            logger.exception(
                f"Pipeline failed unexpectedly: {e}",
                extra={"trace_id": pipeline_run.trace_id, "run_id": pipeline_run.run_id},
            )

        result.completed_at = pipeline_run.completed_at
        return result

    def _execute_stage_with_retry(
        self,
        pipeline_run: PipelineRun,
        stage: PipelineStage,
        payload: dict[str, Any],
        previous_results: dict[str, dict[str, Any]],
        incident_id: int | None,
    ) -> IngestResult | CheckResult | AnalyzeResult | NotifyResult:
        """
        Execute a stage with retry logic.

        Args:
            pipeline_run: The PipelineRun instance.
            stage: Stage to execute.
            payload: Payload for the stage.
            previous_results: Results from previous stages.
            incident_id: Current incident ID.

        Returns:
            Stage result DTO.
        """
        last_error: Exception | None = None
        last_result = None

        for attempt in range(1, self.max_retries + 1):
            # Create stage execution record
            stage_execution = StageExecution.objects.create(
                pipeline_run=pipeline_run,
                stage=stage,
                attempt=attempt,
                idempotency_key=f"{pipeline_run.run_id}:{stage}:{attempt}",
                status=StageStatus.PENDING,
            )

            # Build context
            ctx = StageContext(
                trace_id=pipeline_run.trace_id,
                run_id=pipeline_run.run_id,
                incident_id=incident_id,
                attempt=attempt,
                environment=pipeline_run.environment,
                source=pipeline_run.source,
                alert_fingerprint=pipeline_run.alert_fingerprint,
                payload=payload,
                previous_results=previous_results,
            )

            # Build signal tags
            tags = SignalTags(
                trace_id=pipeline_run.trace_id,
                run_id=pipeline_run.run_id,
                stage=stage,
                incident_id=incident_id,
                source=pipeline_run.source,
                alert_fingerprint=pipeline_run.alert_fingerprint,
                environment=pipeline_run.environment,
                attempt=attempt,
            )

            try:
                # Mark stage started
                stage_execution.mark_started()
                emit_stage_started(tags)

                # Execute
                executor = self.executors[stage]
                result = executor.execute(ctx)
                last_result = result

                # Check for errors
                if result.has_errors and not (
                    stage == PipelineStage.ANALYZE
                    and isinstance(result, AnalyzeResult)
                    and result.fallback_used
                ):
                    raise StageExecutionError(
                        stage=stage,
                        errors=result.errors,
                        retryable=True,
                    )

                # Success
                stage_execution.mark_succeeded(output_snapshot=result.to_dict())
                emit_stage_succeeded(tags, result.duration_ms)
                return result

            except StageExecutionError as e:
                last_error = e
                stage_execution.mark_failed(
                    error_type="StageExecutionError",
                    error_message="; ".join(e.errors),
                    retryable=e.retryable,
                )
                emit_stage_failed(
                    tags,
                    error_type="StageExecutionError",
                    error_message="; ".join(e.errors),
                    retryable=e.retryable,
                    duration_ms=last_result.duration_ms if last_result else 0,
                )

                # Check if retryable
                if not e.retryable or attempt >= self.max_retries:
                    raise

                # Emit retrying and backoff
                emit_stage_retrying(tags)
                backoff_time = self.backoff_factor**attempt
                time.sleep(backoff_time)

            except Exception as e:
                last_error = e
                stage_execution.mark_failed(
                    error_type=type(e).__name__,
                    error_message=str(e),
                    error_stack=traceback.format_exc(),
                    retryable=True,
                )
                emit_stage_failed(
                    tags,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    retryable=True,
                    duration_ms=0,
                )

                if attempt >= self.max_retries:
                    raise

                emit_stage_retrying(tags)
                backoff_time = self.backoff_factor**attempt
                time.sleep(backoff_time)

        # Should not reach here, but raise last error if we do
        if last_error:
            raise last_error

        raise RuntimeError("Stage execution failed without error")

    def _stage_completed(self, pipeline_run: PipelineRun, stage: PipelineStage) -> bool:
        """Check if a stage has already completed successfully."""
        return StageExecution.objects.filter(
            pipeline_run=pipeline_run,
            stage=stage,
            status=StageStatus.SUCCEEDED,
        ).exists()


class StageExecutionError(Exception):
    """Exception raised when a stage fails execution."""

    def __init__(self, stage: str, errors: list[str], retryable: bool = True):
        self.stage = stage
        self.errors = errors
        self.retryable = retryable
        super().__init__(f"Stage {stage} failed: {'; '.join(errors)}")
