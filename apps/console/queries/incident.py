"""Read model for one incident. Returns dataclasses, never querysets."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Alert, Incident
from apps.alerts.timeline import build_incident_timeline
from apps.console.formatting import humanize_duration
from config.admin_badges import (
    CHECK_STATUS_TONES,
    DIAGNOSIS_TONES,
    INCIDENT_STATUS_TONES,
    SEVERITY_TONES,
    STAGE_STATUS_TONES,
    tone_for,
)

STAGE_LABELS = {"ingest": "Ingest", "check": "Diagnose", "analyze": "Analyze", "notify": "Notify"}
SUMMARY_TONES = {**DIAGNOSIS_TONES, "never_ran": "muted"}
# One table for stage and run statuses. A run that ended at an earlier stage's status
# finished the stages its lane asked for.
EXECUTION_TONES = {
    **STAGE_STATUS_TONES,
    "pending": "info",
    "running": "info",
    "retrying": "info",
    "processing": "info",
    "completed": "ok",
    "ingested": "ok",
    "checked": "ok",
    "analyzed": "ok",
    "notified": "ok",
}
DELIVERY_TONES = {"success": "ok", "failed": "critical"}
SEVERITY_EVENTS = frozenset({"created", "refired"})
LIFECYCLE = (
    ("created", "created_at", "Incident opened", "open"),
    ("acknowledged", "acknowledged_at", "Incident acknowledged", "acknowledged"),
    ("resolved", "resolved_at", "Incident resolved", "resolved"),
    ("closed", "closed_at", "Incident closed", "closed"),
)


@dataclass(frozen=True)
class CheckLine:
    name: str
    status: str
    tone: str
    message: str


@dataclass(frozen=True)
class CheckBody:
    kind: ClassVar[str] = "check"
    lines: tuple[CheckLine, ...]
    run: int
    passed: int
    failed: int
    errors: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class Recommendation:
    title: str
    priority: str
    description: str


@dataclass(frozen=True)
class AnalyzeBody:
    kind: ClassVar[str] = "analyze"
    summary: str
    probable_cause: str
    actions: tuple[str, ...]
    recommendations: tuple[Recommendation, ...]
    confidence: int | None
    fallback_used: bool
    errors: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class Delivery:
    provider: str
    channel: str
    status: str
    tone: str
    provider_id: str


@dataclass(frozen=True)
class NotifyBody:
    kind: ClassVar[str] = "notify"
    deliveries: tuple[Delivery, ...]
    attempted: int
    succeeded: int
    failed: int
    errors: tuple[str, ...]
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
    status: str
    tone: str
    detail: str
    runs: str
    url: str


@dataclass(frozen=True)
class TimelineEntry:
    id: str
    when: datetime
    kind: str
    tone: str
    title: str
    detail: str
    url: str
    body: StageBody | None


@dataclass(frozen=True)
class IncidentDetail:
    pk: int
    title: str
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    description: str
    summary: str
    created_at: datetime
    duration: str
    ongoing: bool
    nodes: str
    pipeline: str
    alert_count: int
    admin_url: str
    stages: tuple[StageSummary, ...]
    timeline: tuple[TimelineEntry, ...]


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for text in map(_text, value) if text)


def _dicts(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _percent(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(max(0.0, min(1.0, value)) * 100)


def _check_body(snapshot: dict[str, Any], raw: str) -> CheckBody:
    return CheckBody(
        lines=tuple(
            CheckLine(
                name=_text(check.get("name")),
                status=_text(check.get("status")),
                tone=tone_for(CHECK_STATUS_TONES, _text(check.get("status"))),
                message=_text(check.get("message")),
            )
            for check in _dicts(snapshot.get("checks"))
        ),
        run=_count(snapshot.get("checks_run")),
        passed=_count(snapshot.get("checks_passed")),
        failed=_count(snapshot.get("checks_failed")),
        errors=_texts(snapshot.get("errors")),
        raw=raw,
    )


def _analyze_body(snapshot: dict[str, Any], raw: str) -> AnalyzeBody:
    return AnalyzeBody(
        summary=_text(snapshot.get("summary")),
        probable_cause=_text(snapshot.get("probable_cause")),
        actions=_texts(snapshot.get("actions")),
        recommendations=tuple(
            Recommendation(
                title=_text(item.get("title")),
                priority=_text(item.get("priority")),
                description=_text(item.get("description")),
            )
            for item in _dicts(snapshot.get("recommendations"))
        ),
        confidence=_percent(snapshot.get("confidence")),
        fallback_used=snapshot.get("fallback_used") is True,
        errors=_texts(snapshot.get("errors")),
        raw=raw,
    )


def _notify_body(snapshot: dict[str, Any], raw: str) -> NotifyBody:
    return NotifyBody(
        deliveries=tuple(
            Delivery(
                provider=_text(item.get("provider")),
                channel=_text(item.get("channel")),
                status=_text(item.get("status")),
                tone=tone_for(DELIVERY_TONES, _text(item.get("status"))),
                provider_id=_text(item.get("provider_id")),
            )
            for item in _dicts(snapshot.get("deliveries"))
        ),
        attempted=_count(snapshot.get("channels_attempted")),
        succeeded=_count(snapshot.get("channels_succeeded")),
        failed=_count(snapshot.get("channels_failed")),
        errors=_texts(snapshot.get("errors")),
        raw=raw,
    )


_BODY_BUILDERS: dict[str, Callable[[dict[str, Any], str], StageBody]] = {
    "check": _check_body,
    "analyze": _analyze_body,
    "notify": _notify_body,
}


def stage_body(stage: str, snapshot: object) -> StageBody | None:
    """Renderable form of a stage's stored output. The JSON is untrusted in shape."""
    if not snapshot:
        return None
    raw = json.dumps(snapshot, indent=2, sort_keys=True, default=str)
    builder = _BODY_BUILDERS.get(stage)
    if builder is None or not isinstance(snapshot, dict):
        return RawBody(raw=raw)
    return builder(snapshot, raw)


def history_tone(event: str, severity: str) -> str:
    if event == "resolved":
        return "ok"
    if event in SEVERITY_EVENTS:
        return SEVERITY_TONES.get(severity, "info")
    return "muted"


def execution_tone(status: str) -> str:
    return tone_for(EXECUTION_TONES, status)


def _words(value: str) -> str:
    return value.replace("_", " ")


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
                    tone=tone_for(INCIDENT_STATUS_TONES, status),
                    title=title,
                    detail="",
                    url="",
                    body=None,
                )
            )
    return entries


def _history_entry(event: dict[str, Any]) -> TimelineEntry:
    return TimelineEntry(
        id=f"entry-alert_history-{event['history_pk']}",
        when=event["when"],
        kind="alert_history",
        tone=history_tone(event["event"], event["alert_severity"]),
        title=f"Alert {event['alert_name']} {_words(event['event'])}",
        detail=event["detail"] or "",
        url=reverse("admin:alerts_alert_change", args=[event["alert_pk"]]),
        body=None,
    )


def _pipeline_entry(event: dict[str, Any]) -> TimelineEntry:
    return TimelineEntry(
        id=f"entry-pipeline-{event['run_pk']}",
        when=event["when"],
        kind="pipeline",
        tone=execution_tone(event["status"]),
        title=f"Pipeline run {event['status']}",
        detail=event["run_id"],
        url=reverse("admin:orchestration_pipelinerun_change", args=[event["run_pk"]]),
        body=None,
    )


def _stage_entry(event: dict[str, Any]) -> TimelineEntry:
    title = f"{STAGE_LABELS.get(event['stage'], event['stage'])} {event['status']}"
    if event["duration_ms"]:
        title += f" in {humanize_duration(event['duration_ms'] / 1000)}"
    return TimelineEntry(
        id=f"entry-stage-{event['execution_pk']}",
        when=event["when"],
        kind="stage",
        tone=execution_tone(event["status"]),
        title=title,
        detail=event["detail"] or "",
        url=reverse("admin:orchestration_stageexecution_change", args=[event["execution_pk"]]),
        body=stage_body(event["stage"], event["output_snapshot"]),
    )


_ENTRY_BUILDERS = {
    "alert_history": _history_entry,
    "pipeline": _pipeline_entry,
    "stage": _stage_entry,
}


def _timeline(incident: Incident) -> tuple[TimelineEntry, ...]:
    entries = _lifecycle_entries(incident)
    entries.extend(
        _ENTRY_BUILDERS[event["kind"]](event) for event in build_incident_timeline(incident)
    )
    # The sort is stable, so a lifecycle entry leads anything else at the same instant.
    entries.sort(key=lambda entry: entry.when)
    return tuple(entries)


def _stages(incident: Incident) -> tuple[StageSummary, ...]:
    return tuple(
        StageSummary(
            stage=entry["stage"],
            label=STAGE_LABELS.get(entry["stage"], entry["stage"]),
            status=_words(entry["status"]),
            tone=tone_for(SUMMARY_TONES, entry["status"]),
            detail=entry["detail"] or "",
            runs=entry["runs"] or "",
            url=(
                reverse("admin:orchestration_stageexecution_change", args=[entry["execution_pk"]])
                if entry["execution_pk"] is not None
                else ""
            ),
        )
        for entry in diagnose_incident(incident)
    )


def _nodes(pk: int) -> str:
    seen = (
        Alert.objects.filter(incident_id=pk, node__isnull=False)
        .values_list("node__hostname", "node__instance_id")
        .distinct()
        .order_by()
    )
    names = [hostname or instance_id for hostname, instance_id in seen]
    return ", ".join(sorted(names, key=str.casefold))


def build_incident_detail(pk: int) -> IncidentDetail | None:
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
    return IncidentDetail(
        pk=incident.pk,
        title=incident.title,
        severity=incident.severity,
        severity_tone=tone_for(SEVERITY_TONES, incident.severity),
        status=incident.status,
        status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
        description=incident.description,
        summary=incident.summary,
        created_at=incident.created_at,
        duration=humanize_duration(elapsed.total_seconds()),
        ongoing=ended_at is None,
        nodes=_nodes(incident.pk),
        pipeline=incident.pipeline.name if incident.pipeline else "",
        alert_count=incident.alerts_total,
        admin_url=reverse("admin:alerts_incident_change", args=[incident.pk]),
        stages=_stages(incident),
        timeline=_timeline(incident),
    )
