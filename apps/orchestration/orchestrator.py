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

# Re-exported: many modules and tests import StageExecutionError from here.
from apps.orchestration.errors import StageExecutionError, routing_gap  # noqa: F401
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

        result = self._execute_pipeline(pipeline_run, payload)

        # Drain the children this run enqueued, and only those, through ``self``
        # so the caller's retry/backoff settings and executors apply to them too.
        # See ``inbox.drain_runs`` for why this is not the queue-sweeping drain.
        from apps.orchestration.inbox import drain_runs

        drain_runs(
            PipelineRun.objects.filter(
                trace_id=pipeline_run.trace_id, status=PipelineStatus.PENDING
            ).exclude(run_id=pipeline_run.run_id),
            orchestrator=self,
        )

        return result

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
        # A downstream run IS its stored payload: the one incident it was created
        # for. The resume endpoint builds `payload` from the caller's request body
        # (views.py:229), which cannot describe that — resuming a child with it
        # would drop the marker and send the run back through INGEST against an
        # empty payload. The caller has nothing to add here, so the stored payload
        # wins for children only; a push run still resumes on what it is given.
        if pipeline_run.inbound_payload.get("downstream_incident_id"):
            payload = pipeline_run.inbound_payload
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

        # Two run shapes reach here, and a run IS one of them from the moment it
        # is written.
        #
        # An INCIDENT RUN — ``{"downstream_incident_id": N}`` — is what every
        # producer records (``apps.orchestration.intake.enqueue_for``): the
        # webhook, ``/orchestration/pipeline/``, ``push_to_hub --local``,
        # ``check_health``, ``run_pipeline`` and an operator transition. Its
        # subject is fixed before it exists, so there is nothing to ingest and
        # nothing to re-diagnose a subject from: the lane is resolved straight
        # from the incident's subject alert and exactly those stages run.
        #
        # A LEGACY INGEST RUN — ``{"driver": ..., "payload": ...}`` — is a PENDING
        # row the webhook recorded BEFORE the incident became the subject of a
        # run. No producer writes one any more. It is kept so a push accepted in
        # the seconds before the upgrade still drains rather than being lost: it
        # ingests, fans out one incident run per materially changed incident, and
        # ends there. Delete this branch (and with it ``IngestExecutor``'s place
        # in ``executors``) once no such rows remain in any deployed database.
        downstream_incident_id = payload.get("downstream_incident_id")
        # ``--no-notify``: run the matched lane WITHOUT its NOTIFY stage. The
        # operator SSHes into a node and looks at it in real time — they want the
        # analysis (AnalyzeExecutor's LocalProvider reports top memory processes,
        # large files, cleanable logs) and they want to page nobody. It is a CLI
        # invocation flag scoping one run, so it is read here rather than in the
        # routing engine, the lane table or the executors — the lane that matches
        # is exactly the lane that would have matched; only this run's execution
        # is scoped, and only for as long as the run lasts.
        no_notify = payload.get("no_notify", False)
        active_stages: list[PipelineStage] = []
        if downstream_incident_id:
            # Resolved inside the try below, so a no_route fails the run rather
            # than escaping _execute_pipeline.
            final_status = PipelineStatus.INGESTED  # recomputed once the lane is known
        else:
            # LEGACY: ingest, fan out, stop. INGEST is the whole run, so its
            # status is the terminal one — nothing downstream can move it.
            active_stages = [PipelineStage.INGEST]
            final_status = PipelineStatus.INGESTED

        # Emit pipeline started
        emit_pipeline_started(base_tags)
        # None for an incident run: its first stage is not known until the lane is
        # resolved below, and ``current_stage`` is nullable precisely for that.
        pipeline_run.mark_started(active_stages[0] if active_stages else None)

        # Track previous stage results for context
        previous_results: dict[str, dict[str, Any]] = {}
        incident_id: int | None = None
        alert_id: int | None = None
        if downstream_incident_id:
            # Set BEFORE the loop: every stage is handed this incident_id, which is
            # what NotifyExecutor reads the lane's channel off and what the signal
            # tags carry. No stage in an incident run discovers it.
            incident_id = downstream_incident_id
            alert_id = self._incident_subject_alert_id(downstream_incident_id)
            pipeline_run.incident_id = incident_id
            pipeline_run.save(update_fields=["incident_id", "updated_at"])

        try:
            if downstream_incident_id:
                # Inside the try so a no_route becomes a non-retryable FAILED run
                # like every other routing failure. INGEST is the status floor: an
                # ingest somewhere is what put this incident here.
                #
                # This is also where ``--no-notify`` bites, because this is where
                # NOTIFY actually happens: the run honours the flag its producer
                # was invoked with, carried in its own ``inbound_payload``. The
                # final status is computed from the scoped list, so a run whose
                # NOTIFY was filtered ends ANALYZED exactly as a lane that simply
                # does not list NOTIFY would.
                active_stages.extend(
                    self._without_notify(
                        self._downstream_or_fail(alert_id, pipeline_run.origin), no_notify
                    )
                )
                final_status = self._final_status(active_stages, PipelineStage.INGEST)

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
                        # A legacy ingest run that is resumed skips its INGEST and
                        # still has to say which incident it opened: the fan-out
                        # below reads it, and so do the signal tags. Nothing else
                        # is restored, because nothing else is used — an incident
                        # run knows its subject before the loop starts.
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
                    # CHECK only ever runs here because a lane asked for it, on an
                    # incident that already exists. It records what it found; it
                    # does not get to say what this run is about.
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

            if not downstream_incident_id:
                # LEGACY: the fan-out an incident run no longer needs, because it
                # IS the fan-out. Runs after the loop, so a failure here cannot
                # erase an INGEST the run record already says succeeded.
                snapshot = previous_results.get(PipelineStage.INGEST, {})
                if "material_incident_ids" in snapshot:
                    material = snapshot["material_incident_ids"] or []
                else:
                    # LEGACY-SNAPSHOT COMPATIBILITY — older still than the branch
                    # around it, and it goes when that branch goes. A run whose
                    # INGEST succeeded BEFORE fan-out shipped recorded no material
                    # list, and it can still be re-executed after the upgrade: it
                    # is resumable while FAILED/RETRYING (the resume endpoint, the
                    # admin's "Mark for Retry") and reclaimable while PROCESSING.
                    # Without this its downstream work would vanish silently — no
                    # lane, no analysis, no message for the one incident the old
                    # model would have carried. Filtered to incidents that still
                    # exist, because a resume can happen long after the failure and
                    # a deleted incident would take the whole drain down with an FK
                    # error.
                    material = self._surviving_incident_ids(incident_id)
                self._enqueue_downstream_runs(pipeline_run, material, no_notify=no_notify)

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
    def _surviving_incident_ids(incident_id: int | None) -> list[int]:
        """``[incident_id]`` when that incident is still there, else ``[]``.

        LEGACY-only: read by the pre-fan-out snapshot fallback and nothing else.
        """
        from apps.alerts.models import Incident

        if not incident_id:
            return []
        return list(Incident.objects.filter(id=incident_id).values_list("id", flat=True))

    @staticmethod
    def _without_notify(stages: list[PipelineStage], no_notify: bool) -> list[PipelineStage]:
        """The lane's stages minus NOTIFY when this run was invoked ``--no-notify``.

        The lane row is untouched; this scopes one run's execution only.
        """
        if not no_notify:
            return stages
        return [stage for stage in stages if stage != PipelineStage.NOTIFY]

    def _enqueue_downstream_runs(
        self, parent: PipelineRun, incident_ids: list[int], no_notify: bool = False
    ) -> list[PipelineRun]:
        """Record one PENDING run per materially-changed incident. LEGACY-only.

        Every live producer reaches ``inbox.enqueue_incident_runs`` through
        ``apps.orchestration.intake.enqueue_for`` instead. This wrapper survives
        for the legacy ingest branch alone, and goes when it does.

        Each child carries the parent's ``trace_id`` — so one push is still one
        story in ``manage.py trace`` — with its own ``run_id``, and routes itself
        from its own incident rather than from the push's single subject.

        They are left PENDING for ``process_inbox`` rather than run inline. This is
        NOT a throttle: ``inbox.drain`` still executes up to ``--limit`` runs
        sequentially in one pass, and children only miss the pass that created them
        because ``drain`` snapshots its PK list up front. What it buys is that N
        analyses become N independently claimed, independently retryable,
        crash-isolated runs bounded by ``--limit``, rather than an unbounded loop
        held open inside one run whose crash would lose the lot. ``run_pipeline``
        (the synchronous entry point) drains its own children afterwards, because
        its callers expect one call to finish the job.
        """
        from apps.orchestration.inbox import enqueue_incident_runs

        return enqueue_incident_runs(
            incident_ids,
            trace_id=parent.trace_id,
            origin=parent.origin,
            source=parent.source,
            environment=parent.environment,
            node=parent.node,
            max_retries=self.max_retries,
            parent_run_id=parent.run_id,
            no_notify=no_notify,
        )

    @staticmethod
    def _incident_subject_alert_id(incident_id: int) -> int | None:
        """Subject alert of an incident, by the same rule ingest uses on a batch.

        One caller: an incident run, whose unit of work IS the incident. Its
        producer selected the incident as material and handed nothing else across,
        so the alert this run routes on has to be re-derived here.

        It reads the incident's alerts rather than the push's, which is the
        cross-alert widening the fan-out otherwise avoids — acceptable because the
        incident, not the push, is what this run is about.
        """
        from apps.alerts.models import Alert
        from apps.orchestration.routing import subject_alert

        subject = subject_alert(Alert.objects.filter(incident_id=incident_id))
        return subject.id if subject is not None else None

    def _downstream_stages(self, alert_id: int | None, origin: str) -> list[PipelineStage] | None:
        """This run's stages, from the lane matching its subject alert.

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

        The failure is attributed to ``routing``, NOT to ingest. Ingest is the stage
        that just succeeded — it has a ``StageExecution`` row saying so — and naming
        it here would send an operator to debug the payload, the driver and the
        parser, the one part of the run that worked. ``routing`` is not a
        ``PipelineStage``, which is exactly right: nothing writes this string to a
        ``StageExecution`` row or a signal tag (those use the loop's own ``stage``
        variable); ``StageExecutionError.stage`` is typed ``str`` and is only
        interpolated into the human-readable message on ``PipelineResult`` and
        ``PipelineRun.last_error_message``.
        """
        downstream = self._downstream_stages(alert_id, origin)
        if downstream is None:
            raise routing_gap("routing", "no_route", "no active pipeline matched this alert")
        return downstream

    @staticmethod
    def _final_status(
        downstream: list[PipelineStage], entry_stage: PipelineStage
    ) -> PipelineStatus:
        """Terminal status = the last lane stage that ran, else the floor's.

        The floor is INGEST: an incident exists because something ingested it, so
        a run whose lane lists no stages is INGESTED rather than demoted below the
        state its incident is already in.
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
