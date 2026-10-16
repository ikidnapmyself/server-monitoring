"""Read model for one incident. Returns dataclasses, never querysets."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from apps.alerts.check_integration import can_rerun
from apps.alerts.describing import alert_link, describe_alert, describe_history, describe_incident
from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Incident
from apps.alerts.services import available_incident_actions
from apps.alerts.timeline import build_incident_timeline
from apps.checkers.checkers import CheckStatus
from apps.checkers.describing import check_status, describe_check, describe_check_errors
from apps.console.queries.hosts import incident_hosts
from apps.intelligence.describing import analysis_failed, describe_analysis
from apps.notify.describing import describe_notify
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
# Worst first. OK and skipped results are counted, not listed.
PROBLEM_ORDER = (CheckStatus.CRITICAL, CheckStatus.UNKNOWN, CheckStatus.WARNING)


@dataclass(frozen=True)
class CheckBody:
    kind: ClassVar[str] = "check"
    problems: tuple[Description, ...]
    ok_count: int
    skipped_count: int
    errors: Description | None
    raw: str


@dataclass(frozen=True)
class Recommendation:
    title: str
    priority: str
    description: str


@dataclass(frozen=True)
class AnalyzeBody:
    kind: ClassVar[str] = "analyze"
    outcome: Description
    actions: tuple[str, ...]
    recommendations: tuple[Recommendation, ...]
    raw: str


@dataclass(frozen=True)
class NotifyBody:
    kind: ClassVar[str] = "notify"
    deliveries: tuple[Description, ...]
    raw: str


@dataclass(frozen=True)
class RawBody:
    kind: ClassVar[str] = "raw"
    raw: str


StageBody = CheckBody | AnalyzeBody | NotifyBody | RawBody


@dataclass(frozen=True)
class StageSummary:
    stage: str
    label: str
    outcome: Description


@dataclass(frozen=True)
class TimelineEntry:
    id: str
    when: datetime
    kind: str
    what: Description
    body: StageBody | None


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


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for text in map(_text, value) if text)


def _dicts(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _check_body(snapshot: dict[str, Any], raw: str) -> CheckBody:
    found: dict[CheckStatus, list[Description]] = {status: [] for status in CheckStatus}
    for check in _dicts(snapshot.get("checks")):
        status = check_status(check.get("status"))
        metrics = check.get("metrics")
        found[status].append(
            describe_check(
                _text(check.get("name")),
                status,
                metrics if isinstance(metrics, dict) else {},
                _text(check.get("message")),
            )
        )
    return CheckBody(
        problems=tuple(d for status in PROBLEM_ORDER for d in found[status]),
        ok_count=len(found[CheckStatus.OK]),
        skipped_count=len(found[CheckStatus.SKIPPED]),
        errors=describe_check_errors(_texts(snapshot.get("errors"))),
        raw=raw,
    )


def _analyze_body(snapshot: dict[str, Any], raw: str) -> AnalyzeBody:
    failed = analysis_failed(snapshot)
    return AnalyzeBody(
        outcome=describe_analysis(snapshot),
        actions=() if failed else _texts(snapshot.get("actions")),
        recommendations=(
            ()
            if failed
            else tuple(
                Recommendation(
                    title=_text(item.get("title")),
                    priority=_text(item.get("priority")),
                    description=_text(item.get("description")),
                )
                for item in _dicts(snapshot.get("recommendations"))
            )
        ),
        raw=raw,
    )


def _notify_body(snapshot: dict[str, Any], raw: str) -> NotifyBody:
    return NotifyBody(deliveries=describe_notify(snapshot), raw=raw)


_BODY_BUILDERS: dict[str, tuple[frozenset[str], Callable[[dict[str, Any], str], StageBody]]] = {
    "check": (
        frozenset({"checks", "checks_run", "checks_passed", "checks_failed"}),
        _check_body,
    ),
    "analyze": (
        frozenset(
            {
                "summary",
                "probable_cause",
                "actions",
                "recommendations",
                "confidence",
                "fallback_used",
                "failure",
            }
        ),
        _analyze_body,
    ),
    "notify": (
        frozenset({"deliveries", "channels_attempted", "channels_succeeded", "channels_failed"}),
        _notify_body,
    ),
}


def stage_body(stage: str, snapshot: object) -> StageBody | None:
    """Renderable form of a stage's stored output. The JSON is untrusted in shape.

    A snapshot that is not an object, or that carries none of its stage's keys, is
    shown raw. Old runs stored a different object under the same stage name.
    """
    if not snapshot:
        return None
    raw = json.dumps(snapshot, indent=2, sort_keys=True, default=str, ensure_ascii=False)
    keys, builder = _BODY_BUILDERS.get(stage, (frozenset(), None))
    if builder is None or not isinstance(snapshot, dict) or keys.isdisjoint(snapshot):
        return RawBody(raw=raw)
    return builder(snapshot, raw)


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


def _alert_headlines(incident: Incident) -> dict[int, str]:
    return {alert.pk: describe_alert(alert).headline for alert in incident.alerts.all()}


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
    headlines = _alert_headlines(incident)
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


def _do(incident: Incident, *, can_act: bool, can_rerun_checks: bool) -> tuple[Action, ...]:
    params = {"pk": incident.pk}
    actions = [
        Action(ACTION_LABELS[key], intent=key, params=params)
        for key in (available_incident_actions(incident.status) if can_act else ())
    ]
    if can_rerun_checks and can_rerun(incident):
        actions.append(Action(RERUN, intent="rerun_check", params=params))
    return tuple(actions)


def build_incident_detail(
    pk: int, *, full: bool = False, can_act: bool = False, can_rerun: bool = False
) -> IncidentDetail | None:
    """One incident for its page. ``do`` holds only what the caller may do."""
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
        do=_do(incident, can_act=can_act, can_rerun_checks=can_rerun),
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
