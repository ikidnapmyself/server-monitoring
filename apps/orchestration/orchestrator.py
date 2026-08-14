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
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction

if TYPE_CHECKING:
    from apps.alerts.models import Node

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
    PipelineOrigin,
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
        origin: str | None = None,
    ) -> PipelineRun:
        """
        Start a new pipeline run.

        Creates the PipelineRun record with correlation IDs.

        Args:
            payload: Raw payload to process.
            source: Source system (grafana, alertmanager, etc.).
            trace_id: Optional trace ID (generated if not provided).
            environment: Environment name.
            origin: How the run started (defaults to INCOMING_WEBHOOK).

        Returns:
            Created PipelineRun instance.
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())

        if origin is None:
            origin = PipelineOrigin.INCOMING_WEBHOOK

        run_id = str(uuid.uuid4())

        node = self._resolve_node(payload)

        with transaction.atomic():
            pipeline_run = PipelineRun.objects.create(
                trace_id=trace_id,
                run_id=run_id,
                source=source,
                environment=environment,
                status=PipelineStatus.PENDING,
                max_retries=self.max_retries,
                inbound_payload=payload,
                origin=origin,
                node=node,
            )

        logger.info(
            f"Pipeline started: trace_id={trace_id}, run_id={run_id}",
            extra={"trace_id": trace_id, "run_id": run_id, "source": source},
        )

        return pipeline_run

    @staticmethod
    def _resolve_node(payload: dict[str, Any]) -> "Node | None":
        """Resolve the Node a run concerns.

        Dig the ``instance_id`` out of the wrapper payload — either the inner
        payload's top-level ``instance_id`` (cluster shape) or the first alert's
        labels (instance_id/instance/hostname fallthrough, via the shared,
        malformed-input-safe ``instance_key_from_labels``) — and link to the
        already-registered Node, or None when unknown (e.g. the hub's own
        checker-generated runs, which carry no instance_id).
        """
        from apps.alerts.models import Node
        from apps.alerts.services import instance_key_from_labels

        inner = payload.get("payload")
        if not isinstance(inner, dict):
            return None

        instance_id = inner.get("instance_id")
        if not instance_id:
            alerts = inner.get("alerts")
            if isinstance(alerts, list) and alerts and isinstance(alerts[0], dict):
                instance_id = instance_key_from_labels(alerts[0].get("labels"))

        # instance_id must be a non-empty string before it reaches the ORM filter:
        # a malformed cluster-shape payload could carry a non-str top-level value.
        if not isinstance(instance_id, str) or not instance_id:
            return None
        return Node.objects.filter(instance_id=instance_id).first()

    def run_pipeline(
        self,
        payload: dict[str, Any],
        source: str = "unknown",
        trace_id: str | None = None,
        environment: str = "production",
        origin: str | None = None,
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
            origin: How the run started (defaults to INCOMING_WEBHOOK).

        Returns:
            PipelineResult with all stage results.
        """
        pipeline_run = self.start_pipeline(
            payload=payload,
            source=source,
            trace_id=trace_id,
            environment=environment,
            origin=origin,
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

    def execute_run(self, pipeline_run: PipelineRun) -> PipelineResult:
        """Execute a pre-recorded run using its stored payload (the drain entry point).

        Used by ``manage.py process_inbox`` after it has claimed a PENDING run: the
        run already exists (created by the webhook), so we execute its stored
        ``inbound_payload`` rather than starting a fresh pipeline.
        """
        return self._execute_pipeline(pipeline_run, pipeline_run.inbound_payload)

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

        # Determine which stages to run.
        #
        # checks_only remains a CLI invocation flag (run_pipeline --checks-only),
        # not traffic routing: it selects the ENTRY stage, not the route. One rule
        # covers both branches — the entry stage produces an alert, the lane is
        # resolved from that alert, the lane's stages run. INGEST is the entry
        # stage for webhook traffic; CHECK is the entry stage for the hub's own
        # scheduled checks (bin/install/cron.sh). Either way the downstream stages
        # are resolved AFTER the entry stage (see _downstream_stages), because
        # routing needs that run's subject alert (source/severity/labels/origin).
        checks_only = payload.get("checks_only", False)
        if checks_only:
            active_stages = [PipelineStage.CHECK]
            final_status = PipelineStatus.CHECKED  # recomputed once downstream is known
        else:
            # INGEST is the only stage known up front; the rest are appended once
            # ingest resolves the matched pipeline (runs exactly once per pipeline).
            active_stages = [PipelineStage.INGEST]
            final_status = PipelineStatus.INGESTED  # recomputed once downstream is known

        # The entry stage is the one that produces this run's subject alert, and it
        # is the only stage that routes. A lane is free to list ``check`` as well;
        # a CHECK that runs because a lane asked for it must not re-resolve the
        # route, and when the entry stage is itself CHECK the second pass is simply
        # skipped as already-succeeded (see _stage_completed) — visible, not guarded.
        entry_stage = active_stages[0]
        # ``--checks-only --no-incidents`` is a manual diagnostic: "just check,
        # don't disturb anything". Such a run does not route — no lane, no ANALYZE,
        # no NOTIFY — and ends at CHECKED. Like checks_only itself this is a CLI
        # invocation flag scoping its own run, so it is read here, in the one place
        # invocation flags are read; the routing engine, the lane table and the
        # executors know nothing about it. Alert creation is deliberately NOT
        # suppressed — the bridge still records what it found (that is what
        # --no-incidents already meant); only the downstream fan-out is.
        run_routes = not (checks_only and payload.get("no_incidents", False))
        # Routing happens exactly once per run. This is not a guard on CHECK's
        # execution (that stays unguarded: _stage_completed skips the second pass
        # and the skip is visible in the run record) — it is termination. A lane
        # matching checker-generated traffic that also lists ``check`` appends CHECK
        # to active_stages; without this the skipped second CHECK would re-resolve
        # the same lane and append it again, forever.
        routed = False

        # Emit pipeline started
        emit_pipeline_started(base_tags)
        pipeline_run.mark_started(active_stages[0])

        # Track previous stage results for context
        previous_results: dict[str, dict[str, Any]] = {}
        incident_id: int | None = None
        alert_id: int | None = None

        def route_from_entry_stage(stage: PipelineStage) -> None:
            """Resolve this run's lane, once, from the entry stage's subject alert.

            Both the fresh and the resumed path need the identical rule, and the
            two used to spell it out separately. That is not a style point: the
            paired restore blocks beside them fell out of lockstep and a resumed
            run silently lost its ``incident_id``, which sent notify to the wrong
            channel. One body, called twice, cannot drift.

            It runs LAST for the entry stage, after that stage has been advanced
            and recorded. The subject alert discovered by then is what routes, so
            this cannot move earlier than the entry stage — but it must not move
            earlier than the ``stages_completed`` append either: a ``no_route``
            failure raised here would otherwise erase a stage that demonstrably
            succeeded (its ``StageExecution`` row says so) from the run's record.
            """
            nonlocal routed, final_status
            # ``routed`` alone would be enough today — the loop always reaches the
            # entry stage first, so the first call is always the entry stage's and
            # every later one short-circuits. No test distinguishes the
            # ``entry_stage`` check, and that is deliberate: it states the
            # invariant ("only the entry stage routes") at the point that enforces
            # it, rather than leaving correctness resting on call order.
            if stage != entry_stage or not run_routes or routed:
                return
            routed = True
            downstream = self._downstream_or_fail(alert_id, pipeline_run.origin)
            active_stages.extend(downstream)  # in-place: the loop sees new items
            final_status = self._final_status(downstream, entry_stage)

        try:
            for stage in active_stages:
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
                        # Restore this run's subject from the ENTRY stage's snapshot
                        # — one block for both entry stages, because they need the
                        # same two values. Splitting it per stage is what let the
                        # CHECK copy restore alert_id but not incident_id: notify
                        # then found no incident, could not read the lane's channel,
                        # and delivered to whatever NotifySelector picked instead.
                        # (INGEST is only ever in active_stages as the entry stage,
                        # so this is the same condition the INGEST branch had.)
                        if stage == entry_stage:
                            incident_id = prev_execution.output_snapshot.get("incident_id")
                            alert_id = prev_execution.output_snapshot.get("alert_id")
                            # Legacy fallback stays scoped to INGEST: it exists for
                            # snapshots written before IngestResult had alert_id. A
                            # pre-Task-8 checks_only run never routed at all, so a
                            # CHECK snapshot without alert_id must land on None ->
                            # no downstream -> CHECKED, the behaviour it always had.
                            if stage == PipelineStage.INGEST and not alert_id and incident_id:
                                alert_id = self._legacy_subject_alert_id(incident_id)
                    # Resolve downstream even on resume, so a resumed run still routes.
                    route_from_entry_stage(stage)
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
                    alert_id = stage_result.alert_id
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
                    check_fields = ["checker_output_ref", "updated_at"]
                    if stage == entry_stage:
                        # CHECK is this run's entry stage (run_pipeline --checks-only):
                        # the alerts it just opened are what the lane is resolved
                        # from, and their incident is what notify reads the lane's
                        # channel off and what every signal tag carries. Mirrors the
                        # INGEST block above; incident_id stays None when the checks
                        # opened no incident, which notify handles.
                        alert_id = stage_result.alert_id
                        incident_id = stage_result.incident_id
                        pipeline_run.incident_id = incident_id
                        pipeline_run.alert_fingerprint = stage_result.alert_fingerprint or ""
                        check_fields += ["incident_id", "alert_fingerprint"]
                    pipeline_run.save(update_fields=check_fields)

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
                pipeline_run.advance_to(STAGE_TO_STATUS[stage], stage=stage)
                result.stages_completed.append(stage)

                # Attach result to PipelineResult
                if stage == PipelineStage.INGEST and isinstance(stage_result, IngestResult):
                    result.ingest = stage_result
                elif stage == PipelineStage.CHECK and isinstance(stage_result, CheckResult):
                    result.check = stage_result
                elif stage == PipelineStage.ANALYZE and isinstance(stage_result, AnalyzeResult):
                    result.analyze = stage_result
                elif stage == PipelineStage.NOTIFY and isinstance(  # pragma: no branch
                    stage_result, NotifyResult
                ):
                    result.notify = stage_result

                route_from_entry_stage(stage)

            # Pipeline completed successfully
            duration_ms = (time.perf_counter() - start_time) * 1000
            result.status = "COMPLETED"
            result.incident_id = incident_id
            result.total_duration_ms = duration_ms
            pipeline_run.mark_completed(final_status)

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

    @staticmethod
    def _legacy_subject_alert_id(incident_id: int) -> int | None:
        """Subject alert for an INGEST snapshot written before ``alert_id`` existed.

        LEGACY-SNAPSHOT COMPATIBILITY — delete once no pre-``alert_id`` snapshots
        remain. Such a run is resumable (FAILED/RETRYING, via the resume endpoint
        or the admin's "Mark for Retry"), and without this it would route on
        nothing: every downstream stage silently skipped, the run reported
        COMPLETED, and the incident left unstamped so the diagnosis strip calls
        check/analyze/notify ``never_ran``.

        Re-derives the subject with the same rule ingest uses. It reads the
        incident's alerts rather than this push's, which is exactly the
        cross-alert widening the new code avoids — acceptable only because a
        legacy snapshot has no record of which alerts its push touched.
        """
        from apps.alerts.models import Alert
        from apps.orchestration.routing import subject_alert

        subject = subject_alert(Alert.objects.filter(incident_id=incident_id))
        return subject.id if subject is not None else None

    def _downstream_stages(self, alert_id: int | None, origin: str) -> list[PipelineStage] | None:
        """Stages after the entry stage, from the matched lane.

        ``[]`` means nothing to run downstream — either no alert to route, or a
        lane matched that lists no stages; both complete cleanly. ``None`` means an
        alert exists and no lane claimed it, which the caller turns into a
        non-retryable ``no_route`` failure. There is deliberately no default order
        here: migration ``0012`` seeds a catch-all lane, so "what happens to
        unmatched traffic" is a row an operator can read and edit rather than a
        constant in this file. Delete that row and unmatched traffic fails loudly.
        """
        from apps.alerts.models import Alert
        from apps.orchestration.routing import facts_from_alert, resolve_pipeline

        if not alert_id:
            return []
        # select_related: alert.incident is read below on every matched run.
        alert = Alert.objects.select_related("incident").filter(id=alert_id).first()
        if alert is None:
            return []

        matched = resolve_pipeline(facts_from_alert(alert, origin))
        if matched is None:
            return None

        incident = alert.incident
        if incident is not None and incident.pipeline_id != matched.id:
            incident.pipeline = matched
            incident.save(update_fields=["pipeline", "updated_at"])

        # Normalise on the model: a hand-edited or fixture-written row can hold junk,
        # and an unfiltered PipelineStage(...) would raise ValueError here — swallowed
        # by the generic handler below into a retryable FAILED run that never drains.
        normalised = matched.routable_stages()
        if normalised != matched.stages:
            logger.warning(
                "Pipeline lane %r has an invalid stages value %r; running %r instead",
                matched.name,
                matched.stages,
                normalised,
            )
        return [PipelineStage(s) for s in normalised]

    def _downstream_or_fail(self, alert_id: int | None, origin: str) -> list[PipelineStage]:
        """``_downstream_stages``, turning a no-match into a terminal failure.

        Both call sites (fresh ingest and resume) need the identical rule, so it
        lives here rather than being spelled out twice.

        The failure is attributed to ``routing``, NOT to ingest. Ingest is the stage
        that just succeeded — it has a ``StageExecution`` row saying so — and naming
        it here would send an operator to debug the payload, the driver and the
        parser, the one part of the run that worked. ``routing`` is not a
        ``PipelineStage``, which is exactly right: nothing writes this string to a
        ``StageExecution`` row or a signal tag (those use the loop's own ``stage``
        variable); ``StageExecutionError.stage`` is typed ``str`` and is only
        interpolated into the human-readable message on ``PipelineResult`` and
        ``PipelineRun.last_error_message``.

        ``retryable=False`` because nothing about a retry can conjure a lane: the
        alert is unroutable until an operator adds one, and a retryable failure
        would spin forever.
        """
        downstream = self._downstream_stages(alert_id, origin)
        if downstream is None:
            raise StageExecutionError(
                stage="routing",
                errors=["no_route: no active pipeline matched this alert"],
                retryable=False,
            )
        return downstream

    @staticmethod
    def _final_status(
        downstream: list[PipelineStage], entry_stage: PipelineStage
    ) -> PipelineStatus:
        """Terminal status = the last downstream stage that ran, else the entry stage's.

        The entry stage is the floor because it did run: a webhook run that routes
        nowhere is INGESTED, and a ``--checks-only`` run whose checks touched no
        alerts is CHECKED — not demoted to INGESTED by a stage it never executed.
        """
        if not downstream:
            return STAGE_TO_STATUS[entry_stage]
        return STAGE_TO_STATUS[downstream[-1]]

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
        if last_error:  # pragma: no cover
            raise last_error

        raise RuntimeError("Stage execution failed without error")  # pragma: no cover

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
