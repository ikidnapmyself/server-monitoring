"""Read model for one incident. Returns dataclasses, never querysets."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from apps.alerts.check_integration import can_rerun
from apps.alerts.describing import alert_link, alert_name, describe_history, describe_incident
from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Incident
from apps.alerts.services import available_incident_actions
from apps.alerts.timeline import build_incident_timeline
from apps.console.queries.hosts import incident_hosts
from apps.console.queries.stages import TimelineEntry, stage_body
from apps.orchestration.describing import (
    STAGE_NAMES,
    describe_run_fields,
    describe_stage_diagnosis,
    describe_stage_outcome,
    run_link,
)
from config.admin_badges import INCIDENT_STATUS_TONES, SEVERITY_TONES, tone_for
from config.describing import Action, Description, format_duration, or_action

ACTION_LABELS = {"acknowledge": "Acknowledge", "resolve": "Resolve", "close": "Close"}
RERUN = "Run the checks again"
TIMELINE_LIMIT = 100
LIFECYCLE = (
    ("created", "created_at", "Incident opened", "open"),
    ("acknowledged", "acknowledged_at", "Incident acknowledged", "acknowledged"),
    ("resolved", "resolved_at", "Incident resolved", "resolved"),
    ("closed", "closed_at", "Incident closed", "closed"),
)


@dataclass(frozen=True)
class StageSummary:
    stage: str
    label: str
    outcome: Description


@dataclass(frozen=True)
class IncidentDetail:
    pk: int
    what: Description
    do: tuple[Action, ...]
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    summary: str
    created_at: datetime
    duration: str
    ongoing: bool
    nodes: str
    pipeline: str
    alert_count: int
    url: str
    full_url: str
    recent_url: str
    hidden: int
    stages: tuple[StageSummary, ...]
    timeline: tuple[TimelineEntry, ...]
    full: bool


def _lifecycle_entries(incident: Incident) -> list[TimelineEntry]:
    entries = []
    for name, field, title, status in LIFECYCLE:
        when = getattr(incident, field)
        if when is not None:
            entries.append(
                TimelineEntry(
                    id=f"entry-incident-{name}",
                    when=when,
                    kind="incident",
                    what=Description(title, tone_for(INCIDENT_STATUS_TONES, status)),
                    body=None,
                )
            )
    return entries


def _history_entry(event: dict[str, Any], headlines: dict[int, str]) -> TimelineEntry:
    what = describe_history(
        event["event"],
        alert_headline=headlines.get(event["alert_pk"]) or event["alert_name"] or "An alert",
        old_status=event["old_status"],
        new_status=event["new_status"],
        severity_from=event["severity_from"],
        severity_to=event["severity_to"],
    )
    return TimelineEntry(
        id=f"entry-alert_history-{event['history_pk']}",
        when=event["when"],
        kind="alert_history",
        what=or_action(what, alert_link(event["alert_pk"])),
        body=None,
    )


def _pipeline_entry(event: dict[str, Any], headlines: dict[int, str]) -> TimelineEntry:
    what = describe_run_fields(
        run_id=event["run_id"],
        trace_id=event["trace_id"],
        status=event["status"],
        error_message=event["error_message"],
        finished=event["finished"],
    )
    return TimelineEntry(
        id=f"entry-pipeline-{event['run_pk']}",
        when=event["when"],
        kind="pipeline",
        what=or_action(what, run_link(event["run_pk"])),
        body=None,
    )


def _stage_entry(event: dict[str, Any], headlines: dict[int, str]) -> TimelineEntry:
    what = describe_stage_outcome(
        event["stage"],
        event["status"],
        event["error_message"],
        retryable=event["retryable"],
        duration_ms=event["duration_ms"],
    )
    return TimelineEntry(
        id=f"entry-stage-{event['execution_pk']}",
        when=event["when"],
        kind="stage",
        what=or_action(what, run_link(event["run_pk"])),
        body=stage_body(event["stage"], event["output_snapshot"]),
    )


_ENTRY_BUILDERS = {
    "alert_history": _history_entry,
    "pipeline": _pipeline_entry,
    "stage": _stage_entry,
}


def _alert_names(incident: Incident) -> dict[int, str]:
    return {alert.pk: alert_name(alert) for alert in incident.alerts.all()}


def _timeline(incident: Incident, full: bool) -> tuple[tuple[TimelineEntry, ...], int]:
    """Entries in time order, and how many recorded events exist.

    Unless ``full``, only the newest ``TIMELINE_LIMIT`` events are kept. Lifecycle
    entries always stay. The cut happens before entries are built, so a dropped
    stage never pays for its body.
    """
    events = build_incident_timeline(incident)
    recorded = len(events)
    if not full:
        events = events[-TIMELINE_LIMIT:]
    headlines = _alert_names(incident)
    entries = _lifecycle_entries(incident)
    entries.extend(_ENTRY_BUILDERS[event["kind"]](event, headlines) for event in events)
    # The sort is stable, so a lifecycle entry leads anything else at the same instant.
    entries.sort(key=lambda entry: entry.when)
    return tuple(entries), recorded


def _stages(incident: Incident) -> tuple[StageSummary, ...]:
    return tuple(
        StageSummary(
            stage=entry["stage"],
            label=STAGE_NAMES.get(entry["stage"], entry["stage"]),
            outcome=describe_stage_diagnosis(entry),
        )
        for entry in diagnose_incident(incident)
    )


def _do(incident: Incident) -> tuple[Action, ...]:
    params = {"pk": incident.pk}
    actions = [
        Action(ACTION_LABELS[key], intent=key, params=params)
        for key in available_incident_actions(incident.status)
    ]
    if can_rerun(incident):
        actions.append(Action(RERUN, intent="rerun_check", params=params))
    return tuple(actions)


def build_incident_detail(pk: int, *, full: bool = False) -> IncidentDetail | None:
    """One incident for its page."""
    incident = (
        Incident.objects.select_related("pipeline")
        .annotate(alerts_total=Count("alerts"))
        .filter(pk=pk)
        .first()
    )
    if incident is None:
        return None
    ended_at = incident.resolved_at or incident.closed_at
    elapsed = (ended_at or timezone.now()) - incident.created_at
    timeline, recorded = _timeline(incident, full)
    over = max(0, recorded - TIMELINE_LIMIT)
    recent_url = reverse("console:incident", args=[incident.pk])
    full_url = f"{recent_url}?all=1"
    return IncidentDetail(
        pk=incident.pk,
        what=describe_incident(incident),
        do=_do(incident),
        severity=incident.severity,
        severity_tone=tone_for(SEVERITY_TONES, incident.severity),
        status=incident.status,
        status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
        summary=incident.summary,
        created_at=incident.created_at,
        duration=format_duration(elapsed.total_seconds()),
        ongoing=ended_at is None,
        nodes=", ".join(incident_hosts([incident.pk]).get(incident.pk, ())),
        pipeline=incident.pipeline.name if incident.pipeline else "",
        alert_count=incident.alerts_total,
        url=full_url if full else recent_url,
        full_url=full_url,
        recent_url=recent_url if full and over else "",
        hidden=0 if full else over,
        stages=_stages(incident),
        timeline=timeline,
        full=full,
    )
