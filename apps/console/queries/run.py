"""Read model for one pipeline run's page. Returns dataclasses, never querysets."""

import json
from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import F
from django.urls import reverse

from apps.console.queries.alert import IncidentCard, incident_card
from apps.console.queries.hosts import alert_host, incident_hosts
from apps.console.queries.runs import origin_label
from apps.console.queries.stages import TimelineEntry, stage_body
from apps.orchestration.describing import STAGE_NAMES, describe_run, describe_stage_outcome
from apps.orchestration.inbox import abandonable
from apps.orchestration.models import PipelineRun, PipelineStatus, StageExecution
from config.describing import Action, Description, Fact

RETRY = "Retry"
MARK_FAILED = "Mark failed"
REFS = (
    ("Normalized payload", "normalized_payload_ref"),
    ("Checker output", "checker_output_ref"),
    ("Analysis output", "intelligence_output_ref"),
    ("Notify output", "notify_output_ref"),
)


@dataclass(frozen=True)
class RunDetail:
    pk: int
    what: Description
    do: tuple[Action, ...]
    incident: IncidentCard | None
    node: str
    origin: str
    stage: str
    attempts: int
    created_at: datetime
    timeline: tuple[TimelineEntry, ...]
    raw: str
    url: str


def _do(run: PipelineRun) -> tuple[Action, ...]:
    params = {"pk": run.pk}
    if run.status == PipelineStatus.FAILED:
        return (Action(RETRY, "retry_run", params),)
    if PipelineRun.objects.filter(abandonable(), pk=run.pk).exists():
        return (Action(MARK_FAILED, "abandon_run", params),)
    return ()


def _facts(run: PipelineRun) -> tuple[Fact, ...]:
    facts = [Fact("Fingerprint", run.alert_fingerprint, copy=True)] if run.alert_fingerprint else []
    facts += [Fact(label, getattr(run, field)) for label, field in REFS if getattr(run, field)]
    if run.last_error_type:
        facts += [
            Fact("Error type", run.last_error_type),
            Fact("Retryable", "yes" if run.last_error_retryable else "no"),
        ]
    return tuple(facts)


def _entry(execution: StageExecution, fallback: datetime) -> TimelineEntry:
    what = describe_stage_outcome(
        execution.stage,
        execution.status,
        execution.error_message,
        retryable=execution.error_retryable,
        duration_ms=execution.duration_ms,
    )
    return TimelineEntry(
        id=f"stage-{execution.pk}",
        when=execution.started_at or fallback,
        kind="stage",
        what=replace(what, facts=(*what.facts, Fact("Attempt", str(execution.attempt)))),
        body=stage_body(execution.stage, execution.output_snapshot),
    )


def run_exists(pk: int) -> bool:
    return PipelineRun.objects.filter(pk=pk).exists()


def build_run_detail(pk: int) -> RunDetail | None:
    """One run for its page."""
    run = PipelineRun.objects.select_related("node", "incident").filter(pk=pk).first()
    if run is None:
        return None
    executions = run.stage_executions.order_by(
        F("started_at").asc(nulls_last=True), "attempt", "pk"
    )
    what = describe_run(run)
    if run.node:
        node = alert_host(run.node.hostname, run.node.instance_id, None)
    elif run.incident_id:
        node = ", ".join(incident_hosts([run.incident_id]).get(run.incident_id, ()))
    else:
        node = ""
    return RunDetail(
        pk=run.pk,
        what=replace(what, facts=(*what.facts, *_facts(run))),
        do=_do(run),
        incident=incident_card(run.incident),
        node=node,
        origin=origin_label(run.origin),
        stage=STAGE_NAMES.get(run.current_stage or "", ""),
        attempts=run.total_attempts,
        created_at=run.created_at,
        timeline=tuple(_entry(e, run.created_at) for e in executions),
        raw=json.dumps(run.inbound_payload, indent=2, sort_keys=True, default=str),
        url=reverse("console:run", args=[run.pk]),
    )
