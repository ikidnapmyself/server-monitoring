import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.console.queries import incident as incident_queries
from apps.console.queries.incident import (
    AnalyzeBody,
    CheckBody,
    CheckLine,
    Delivery,
    NotifyBody,
    RawBody,
    Recommendation,
    build_incident_detail,
    stage_body,
)
from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=dt_timezone.utc)

CHECK_SNAPSHOT = {
    "alert_id": 15,
    "incident_id": 13,
    "checks": [
        {
            "name": "cpu",
            "status": "ok",
            "message": "CPU usage: 21.8% (avg of 5 samples)",
            "metrics": {"cpu_percent": 21.8},
        },
        {
            "name": "memory",
            "status": "warning",
            "message": "Memory usage: 80.6% (3.9/8.0 GB)",
            "metrics": {"percent": 80.6},
        },
    ],
    "checks_run": 2,
    "checks_passed": 1,
    "checks_failed": 1,
    "errors": ["disk: timed out"],
    "duration_ms": 12.5,
}
ANALYZE_SNAPSHOT = {
    "summary": "Memory is tight on web-1.",
    "probable_cause": "A leak in the worker.",
    "actions": ["Restart the worker"],
    "recommendations": [
        {
            "type": "general",
            "priority": "medium",
            "title": "Add swap",
            "description": "The box has none.",
            "details": {},
            "actions": ["swapon"],
            "incident_id": 13,
        }
    ],
    "confidence": 0.8,
    "fallback_used": True,
    "errors": [],
}
NOTIFY_SNAPSHOT = {
    "deliveries": [
        {
            "driver": "felixlaan",
            "provider": "slack",
            "channel": "#alerts",
            "status": "success",
            "provider_id": "a970",
            "response": {"ok": True},
        },
        {"provider": "email", "channel": "ops@example.com", "status": "failed"},
    ],
    "provider_ids": ["a970"],
    "messages": [],
    "channels_attempted": 2,
    "channels_succeeded": 1,
    "channels_failed": 1,
    "errors": ["email: refused"],
}


def _incident(created_at=T0, **fields):
    incident = Incident.objects.create(**{"title": "Disk full", **fields})
    Incident.objects.filter(pk=incident.pk).update(created_at=created_at)
    incident.refresh_from_db()
    return incident


def _alert(incident, node=None, key="a", severity="warning"):
    return Alert.objects.create(
        fingerprint=f"{incident.pk}:{key}",
        name=key,
        severity=severity,
        status="firing",
        source="cluster",
        node=node,
        incident=incident,
        started_at=T0,
    )


def _history(alert, event, when, **fields):
    row = AlertHistory.objects.create(alert=alert, event=event, **fields)
    AlertHistory.objects.filter(pk=row.pk).update(created_at=when)
    return row


def _run(incident, when, status="notified", run_id="run-1"):
    run = PipelineRun.objects.create(
        trace_id=f"trace-{run_id}",
        run_id=run_id,
        incident=incident,
        status=status,
        inbound_payload={"downstream_incident_id": incident.pk},
    )
    PipelineRun.objects.filter(pk=run.pk).update(created_at=when)
    return run


def _stage(run, stage, when, status="succeeded", **fields):
    return StageExecution.objects.create(
        pipeline_run=run, stage=stage, status=status, started_at=when, **fields
    )


def _entry(detail, entry_id):
    (entry,) = [entry for entry in detail.timeline if entry.id == entry_id]
    return entry


def _raw(snapshot):
    return json.dumps(snapshot, indent=2, sort_keys=True, default=str, ensure_ascii=False)


@pytest.mark.django_db
def test_a_missing_incident_is_none():
    assert build_incident_detail(404) is None


@pytest.mark.django_db
def test_header_fields_describe_the_incident():
    pipeline = PipelineDefinition.objects.create(name="pager", stages=["check", "notify"])
    incident = _incident(
        title="Disk full on web-1",
        severity="critical",
        status="acknowledged",
        description="92% used",
        summary="Logs rotated",
        pipeline=pipeline,
    )
    web2 = Node.objects.create(instance_id="id-2", hostname="web-2")
    bare = Node.objects.create(instance_id="Alpha")
    _alert(incident, web2, key="a")
    _alert(incident, web2, key="b")
    _alert(incident, bare, key="c")
    _alert(incident, None, key="d")

    detail = build_incident_detail(incident.pk)

    assert detail.pk == incident.pk
    assert detail.title == "Disk full on web-1"
    assert (detail.severity, detail.severity_tone) == ("critical", "critical")
    assert (detail.status, detail.status_tone) == ("acknowledged", "warning")
    assert detail.description == "92% used"
    assert detail.summary == "Logs rotated"
    assert detail.created_at == T0
    assert detail.nodes == "Alpha, web-2"
    assert detail.pipeline == "pager"
    assert detail.alert_count == 4
    assert detail.admin_url == reverse("admin:alerts_incident_change", args=[incident.pk])
    assert detail.url == f"/console/incidents/{incident.pk}/"
    assert detail.full_url == f"/console/incidents/{incident.pk}/?all=1"
    assert detail.recent_url == ""
    assert detail.hidden == 0
    assert isinstance(detail.stages, tuple)
    assert isinstance(detail.timeline, tuple)
    with pytest.raises(FrozenInstanceError):
        detail.title = "x"


@pytest.mark.django_db
def test_an_unrouted_incident_with_no_alerts_has_empty_header_strings():
    detail = build_incident_detail(_incident().pk)

    assert detail.nodes == ""
    assert detail.pipeline == ""
    assert detail.alert_count == 0


@pytest.mark.django_db
def test_an_open_incident_is_timed_up_to_now(monkeypatch):
    monkeypatch.setattr(
        incident_queries.timezone, "now", lambda: T0 + timedelta(minutes=4, seconds=12)
    )

    detail = build_incident_detail(_incident().pk)

    assert detail.duration == "4m 12s"
    assert detail.ongoing is True


@pytest.mark.django_db
def test_a_resolved_incident_is_timed_up_to_its_resolution():
    incident = _incident(
        status="closed",
        resolved_at=T0 + timedelta(hours=3, minutes=5),
        closed_at=T0 + timedelta(days=9),
    )

    detail = build_incident_detail(incident.pk)

    assert detail.duration == "3h 05m"
    assert detail.ongoing is False


@pytest.mark.django_db
def test_an_incident_closed_without_a_resolution_is_timed_up_to_its_close():
    incident = _incident(status="closed", closed_at=T0 + timedelta(days=2, hours=4))

    detail = build_incident_detail(incident.pk)

    assert detail.duration == "2d 4h"
    assert detail.ongoing is False


@pytest.mark.django_db
def test_lifecycle_entries_come_from_the_incident_timestamps():
    incident = _incident(
        status="closed",
        acknowledged_at=T0 + timedelta(minutes=1),
        resolved_at=T0 + timedelta(minutes=2),
        closed_at=T0 + timedelta(minutes=3),
    )

    detail = build_incident_detail(incident.pk)

    assert [
        (entry.id, entry.kind, entry.title, entry.tone, entry.when) for entry in detail.timeline
    ] == [
        ("entry-incident-created", "incident", "Incident opened", "critical", T0),
        (
            "entry-incident-acknowledged",
            "incident",
            "Incident acknowledged",
            "warning",
            T0 + timedelta(minutes=1),
        ),
        (
            "entry-incident-resolved",
            "incident",
            "Incident resolved",
            "ok",
            T0 + timedelta(minutes=2),
        ),
        (
            "entry-incident-closed",
            "incident",
            "Incident closed",
            "muted",
            T0 + timedelta(minutes=3),
        ),
    ]
    assert all((entry.url, entry.detail, entry.body) == ("", "", None) for entry in detail.timeline)


@pytest.mark.django_db
def test_an_open_incident_has_only_the_opened_lifecycle_entry():
    detail = build_incident_detail(_incident().pk)

    assert [entry.id for entry in detail.timeline] == ["entry-incident-created"]


@pytest.mark.django_db
def test_entries_sort_by_time_with_lifecycle_first_on_a_tie():
    incident = _incident(acknowledged_at=T0 + timedelta(seconds=30), status="acknowledged")
    alert = _alert(incident)
    created = _history(alert, "created", T0)
    refired = _history(alert, "refired", T0 + timedelta(seconds=30))
    run = _run(incident, T0 + timedelta(seconds=10))
    stage = _stage(run, "check", T0 + timedelta(seconds=20))

    detail = build_incident_detail(incident.pk)

    assert [entry.id for entry in detail.timeline] == [
        "entry-incident-created",
        f"entry-alert_history-{created.pk}",
        f"entry-pipeline-{run.pk}",
        f"entry-stage-{stage.pk}",
        "entry-incident-acknowledged",
        f"entry-alert_history-{refired.pk}",
    ]
    assert len({entry.id for entry in detail.timeline}) == len(detail.timeline)


@pytest.mark.django_db
def test_alert_history_entries_read_as_sentences_and_link_the_alert():
    incident = _incident()
    alert = _alert(incident, key="cpu high", severity="critical")
    created = _history(alert, "created", T0 + timedelta(seconds=1), new_status="firing")
    changed = _history(
        alert,
        "severity_changed",
        T0 + timedelta(seconds=2),
        details={"severity_from": "warning", "severity_to": "critical"},
    )
    resolved = _history(alert, "resolved", T0 + timedelta(seconds=3))

    detail = build_incident_detail(incident.pk)

    entry = _entry(detail, f"entry-alert_history-{created.pk}")
    assert entry.kind == "alert_history"
    assert entry.title == "Alert cpu high created"
    assert entry.detail == "— → firing"
    assert entry.url == reverse("admin:alerts_alert_change", args=[alert.pk])
    assert entry.tone == "info"
    assert entry.body is None
    entry = _entry(detail, f"entry-alert_history-{changed.pk}")
    assert entry.title == "Alert cpu high severity changed"
    assert entry.detail == "severity warning → critical"
    assert entry.tone == "critical"
    entry = _entry(detail, f"entry-alert_history-{resolved.pk}")
    assert entry.detail == ""
    assert entry.tone == "ok"


@pytest.mark.parametrize(
    ("event", "severity_to", "tone"),
    [
        ("created", None, "info"),
        ("refired", None, "info"),
        ("resolved", None, "ok"),
        ("updated", None, "muted"),
        ("severity_changed", "critical", "critical"),
        ("reevaluated", "warning", "warning"),
        ("created", "critical", "critical"),
        ("created", "bogus", "info"),
        ("updated", ["critical"], "muted"),
        ("resolved", {"a": 1}, "ok"),
    ],
)
def test_alert_history_tones(event, severity_to, tone):
    assert incident_queries.history_tone(event, severity_to) == tone


@pytest.mark.parametrize(
    ("status", "tone"),
    [
        ("succeeded", "ok"),
        ("completed", "ok"),
        ("notified", "ok"),
        ("ingested", "info"),
        ("checked", "info"),
        ("analyzed", "info"),
        ("failed", "critical"),
        ("running", "info"),
        ("pending", "info"),
        ("retrying", "info"),
        ("processing", "info"),
        ("skipped", "muted"),
        ("bogus", "muted"),
    ],
)
def test_run_and_stage_tones(status, tone):
    assert incident_queries.execution_tone(status) == tone


@pytest.mark.django_db
def test_run_and_stage_entries_read_as_sentences_and_link_the_admin():
    incident = _incident()
    run = _run(incident, T0 + timedelta(seconds=1), status="failed", run_id="run-9")
    check = _stage(
        run, "check", T0 + timedelta(seconds=2), duration_ms=850.0, output_snapshot=CHECK_SNAPSHOT
    )
    notify = _stage(
        run, "notify", T0 + timedelta(seconds=3), status="failed", error_message="slack 500"
    )
    odd = _stage(run, "custom", T0 + timedelta(seconds=4), status="running")

    detail = build_incident_detail(incident.pk)

    entry = _entry(detail, f"entry-pipeline-{run.pk}")
    assert entry.kind == "pipeline"
    assert entry.title == "Pipeline run started"
    assert entry.detail == "failed · run-9"
    assert entry.tone == "critical"
    assert entry.url == reverse("admin:orchestration_pipelinerun_change", args=[run.pk])
    assert entry.body is None
    entry = _entry(detail, f"entry-stage-{check.pk}")
    assert entry.kind == "stage"
    assert entry.title == "Diagnose succeeded in 850ms"
    assert entry.detail == ""
    assert entry.tone == "ok"
    assert entry.url == reverse("admin:orchestration_stageexecution_change", args=[check.pk])
    assert isinstance(entry.body, CheckBody)
    entry = _entry(detail, f"entry-stage-{notify.pk}")
    assert entry.title == "Notify failed"
    assert entry.detail == "slack 500"
    assert entry.tone == "critical"
    assert entry.body is None
    entry = _entry(detail, f"entry-stage-{odd.pk}")
    assert entry.title == "custom running"
    assert entry.tone == "info"


@pytest.mark.django_db
def test_stage_summaries_follow_the_diagnosis():
    pipeline = PipelineDefinition.objects.create(name="no-ai", stages=["check", "notify"])
    incident = _incident(pipeline=pipeline)
    run = _run(incident, T0)
    check = _stage(run, "check", T0, output_snapshot=CHECK_SNAPSHOT)

    detail = build_incident_detail(incident.pk)

    assert [
        (s.stage, s.label, s.status, s.tone, s.detail, s.runs, s.url) for s in detail.stages
    ] == [
        (
            "check",
            "Diagnose",
            "ok",
            "ok",
            "",
            "succeeded in 1/1 runs",
            reverse("admin:orchestration_stageexecution_change", args=[check.pk]),
        ),
        (
            "analyze",
            "Analyze",
            "skipped",
            "muted",
            "config: analyze not in the pipeline's stages",
            "",
            "",
        ),
        ("notify", "Notify", "never ran", "muted", "", "succeeded in 0/1 runs", ""),
    ]


@pytest.mark.django_db
def test_a_legacy_run_adds_the_ingest_summary():
    incident = _incident()
    run = PipelineRun.objects.create(trace_id="t", run_id="legacy", incident=incident)
    _stage(run, "ingest", T0, status="running")
    _stage(run, "check", T0, status="failed", error_type="Boom", error_message="no disk")
    _stage(run, "analyze", T0, status="succeeded")

    detail = build_incident_detail(incident.pk)

    assert [(s.label, s.status, s.tone) for s in detail.stages] == [
        ("Ingest", "stalled", "warning"),
        ("Diagnose", "failed", "critical"),
        ("Analyze", "empty", "warning"),
        ("Notify", "never ran", "muted"),
    ]
    assert detail.stages[1].detail == "Boom: no disk (retryable=True)"


def test_a_check_snapshot_becomes_lines_and_counts():
    body = stage_body("check", CHECK_SNAPSHOT)

    assert body == CheckBody(
        lines=(
            CheckLine("cpu", "ok", "ok", "CPU usage: 21.8% (avg of 5 samples)"),
            CheckLine("memory", "warning", "warning", "Memory usage: 80.6% (3.9/8.0 GB)"),
        ),
        run=2,
        passed=1,
        failed=1,
        errors=("disk: timed out",),
        raw=_raw(CHECK_SNAPSHOT),
    )
    assert body.kind == "check"
    assert '"checks_run": 2' in body.raw
    assert body.raw.index('"alert_id"') < body.raw.index('"checks"')


def test_an_analyze_snapshot_becomes_prose_and_recommendations():
    body = stage_body("analyze", ANALYZE_SNAPSHOT)

    assert body == AnalyzeBody(
        summary="Memory is tight on web-1.",
        probable_cause="A leak in the worker.",
        actions=("Restart the worker",),
        recommendations=(Recommendation("Add swap", "medium", "The box has none."),),
        confidence=80,
        fallback_used=True,
        errors=(),
        raw=_raw(ANALYZE_SNAPSHOT),
    )
    assert body.kind == "analyze"


def test_a_notify_snapshot_becomes_deliveries_and_counts():
    body = stage_body("notify", NOTIFY_SNAPSHOT)

    assert body == NotifyBody(
        deliveries=(
            Delivery("slack", "#alerts", "success", "ok", "a970"),
            Delivery("email", "ops@example.com", "failed", "critical", ""),
        ),
        attempted=2,
        succeeded=1,
        failed=1,
        errors=("email: refused",),
        raw=_raw(NOTIFY_SNAPSHOT),
    )
    assert body.kind == "notify"


@pytest.mark.parametrize("stage", ["check", "analyze", "notify", "ingest", "custom"])
@pytest.mark.parametrize("snapshot", [None, {}, [], "", 0])
def test_an_empty_snapshot_has_no_body(stage, snapshot):
    assert stage_body(stage, snapshot) is None


@pytest.mark.parametrize("stage", ["check", "analyze", "notify"])
@pytest.mark.parametrize("snapshot", [["a", {"b": 1}], "just text", 7, True])
def test_a_snapshot_that_is_not_an_object_is_shown_raw(stage, snapshot):
    assert stage_body(stage, snapshot) == RawBody(raw=_raw(snapshot))
    assert stage_body(stage, snapshot).kind == "raw"


@pytest.mark.parametrize("stage", ["ingest", "custom"])
def test_a_stage_without_a_renderer_is_shown_raw(stage):
    snapshot = {"alerts_created": 1}

    assert stage_body(stage, snapshot) == RawBody(raw=_raw(snapshot))


@pytest.mark.parametrize("stage", ["check", "analyze", "notify"])
def test_an_object_with_none_of_the_stage_keys_is_shown_raw(stage):
    snapshot = {"node_id": "n1", "output": {}, "skipped": True, "errors": ["boom"]}

    assert stage_body(stage, snapshot) == RawBody(raw=_raw(snapshot))


def test_a_malformed_check_snapshot_does_not_raise():
    snapshot = {
        "checks": [
            "cpu",
            None,
            {"name": ["x"], "status": {"a": 1}, "message": None},
            {"name": 7, "status": "bogus", "message": 1.5},
        ],
        "checks_run": "two",
        "checks_passed": None,
        "checks_failed": True,
        "errors": "boom",
    }

    assert stage_body("check", snapshot) == CheckBody(
        lines=(CheckLine("", "", "muted", ""), CheckLine("7", "bogus", "muted", "1.5")),
        run=0,
        passed=0,
        failed=0,
        errors=(),
        raw=_raw(snapshot),
    )
    assert stage_body("check", {"checks": {"name": "cpu"}}).lines == ()


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.8, 80),
        (1, 100),
        (0, 0),
        (7.5, 100),
        (-1, 0),
        (1e308, 100),
        (10**400, 100),
        (-(10**400), 0),
        (float("nan"), None),
        (float("inf"), None),
        (True, None),
        ("0.8", None),
        (None, None),
        ([0.8], None),
    ],
)
def test_confidence_is_a_clamped_percent_or_nothing(confidence, expected):
    assert stage_body("analyze", {"confidence": confidence}).confidence == expected


def test_a_malformed_analyze_snapshot_does_not_raise():
    snapshot = {
        "summary": ["not", "text"],
        "probable_cause": {"a": 1},
        "actions": ["ok", 3, None, {"x": 1}, ""],
        "recommendations": ["text", {"title": None, "priority": 2, "description": []}],
        "fallback_used": "yes",
        "errors": [["nested"], "real"],
    }

    assert stage_body("analyze", snapshot) == AnalyzeBody(
        summary="",
        probable_cause="",
        actions=("ok", "3"),
        recommendations=(Recommendation("", "2", ""),),
        confidence=None,
        fallback_used=False,
        errors=("real",),
        raw=_raw(snapshot),
    )
    assert stage_body("analyze", {"actions": "restart", "recommendations": 5}).actions == ()


def test_a_malformed_notify_snapshot_does_not_raise():
    snapshot = {
        "deliveries": [None, "slack", {"provider": 1, "status": None, "provider_id": 42}],
        "channels_attempted": "1",
        "channels_succeeded": 1.5,
        "channels_failed": [],
        "errors": {"a": "b"},
    }

    assert stage_body("notify", snapshot) == NotifyBody(
        deliveries=(Delivery("1", "", "", "muted", "42"),),
        attempted=0,
        succeeded=0,
        failed=0,
        errors=(),
        raw=_raw(snapshot),
    )
    assert stage_body("notify", {"deliveries": "none"}).deliveries == ()


@pytest.mark.django_db
def test_a_stored_snapshot_of_the_wrong_shape_still_builds_the_page_model():
    incident = _incident()
    run = _run(incident, T0)
    listed = _stage(run, "check", T0, output_snapshot=["not", "a", "dict"])
    texted = _stage(run, "analyze", T0, output_snapshot="just text")
    empty = _stage(run, "notify", T0, output_snapshot={})

    detail = build_incident_detail(incident.pk)

    assert isinstance(_entry(detail, f"entry-stage-{listed.pk}").body, RawBody)
    assert _entry(detail, f"entry-stage-{texted.pk}").body == RawBody(raw='"just text"')
    assert _entry(detail, f"entry-stage-{empty.pk}").body is None


def _populate(incident, count):
    for index in range(count):
        node = Node.objects.create(instance_id=f"{incident.pk}-{index}")
        alert = _alert(incident, node, key=str(index))
        _history(alert, "created", T0)
        _history(alert, "refired", T0)
        run = _run(incident, T0, run_id=f"run-{incident.pk}-{index}")
        _stage(run, "check", T0, output_snapshot=CHECK_SNAPSHOT)
        _stage(run, "notify", T0, output_snapshot=NOTIFY_SNAPSHOT)


@pytest.mark.django_db
def test_the_query_count_does_not_grow_with_the_incident():
    pipeline = PipelineDefinition.objects.create(name="lane", stages=["check", "notify"])
    small, large = _incident(pipeline=pipeline), _incident(pipeline=pipeline)
    _populate(small, 1)
    _populate(large, 6)

    with CaptureQueriesContext(connection) as few:
        build_incident_detail(small.pk)
    with CaptureQueriesContext(connection) as many:
        detail = build_incident_detail(large.pk)

    assert len(detail.timeline) == 1 + 6 * 5
    assert len(few) == len(many) == 7


def test_raw_json_keeps_non_ascii_text_readable():
    assert stage_body("ingest", {"note": "Disk doldu, çöp topla"}).raw == (
        '{\n  "note": "Disk doldu, çöp topla"\n}'
    )


def _long(count):
    """A resolved incident with ``count`` stage entries, oldest first, plus two lifecycle."""
    incident = _incident(status="resolved", resolved_at=T0 + timedelta(minutes=30))
    run = _run(incident, T0 - timedelta(hours=1))
    stages = [
        _stage(
            run,
            "check",
            T0 + timedelta(minutes=index),
            attempt=index,
            output_snapshot=CHECK_SNAPSHOT,
        )
        for index in range(1, count)
    ]
    return incident, run, stages


@pytest.mark.django_db
def test_a_timeline_at_the_limit_is_shown_whole(monkeypatch):
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 4)
    incident, run, stages = _long(4)

    detail = build_incident_detail(incident.pk)

    assert detail.hidden == 0
    assert detail.recent_url == ""
    assert [entry.id for entry in detail.timeline] == [
        f"entry-pipeline-{run.pk}",
        "entry-incident-created",
        *(f"entry-stage-{stage.pk}" for stage in stages),
        "entry-incident-resolved",
    ]
    assert build_incident_detail(incident.pk, full=True).recent_url == ""


@pytest.mark.django_db
def test_a_timeline_over_the_limit_keeps_lifecycle_and_the_newest_entries(monkeypatch):
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 4)
    incident, run, stages = _long(7)
    built = []
    stage_body = incident_queries.stage_body
    monkeypatch.setattr(
        incident_queries, "stage_body", lambda *args: built.append(args) or stage_body(*args)
    )

    detail = build_incident_detail(incident.pk)

    assert detail.hidden == 3
    assert [entry.id for entry in detail.timeline] == [
        "entry-incident-created",
        *(f"entry-stage-{stage.pk}" for stage in stages[2:]),
        "entry-incident-resolved",
    ]
    assert len(built) == 4
    assert detail.url == f"/console/incidents/{incident.pk}/"
    assert detail.full_url == f"/console/incidents/{incident.pk}/?all=1"
    assert detail.recent_url == ""


@pytest.mark.django_db
def test_one_entry_over_the_limit_hides_one(monkeypatch):
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 4)
    incident, run, stages = _long(5)

    detail = build_incident_detail(incident.pk)

    assert detail.hidden == 1
    assert f"entry-pipeline-{run.pk}" not in [entry.id for entry in detail.timeline]
    assert len(detail.timeline) == 4 + 2


@pytest.mark.django_db
def test_the_full_view_shows_everything_and_polls_itself(monkeypatch):
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 4)
    incident, run, stages = _long(7)

    detail = build_incident_detail(incident.pk, full=True)

    assert detail.hidden == 0
    assert len(detail.timeline) == 7 + 2
    assert detail.timeline[0].id == f"entry-pipeline-{run.pk}"
    assert detail.url == detail.full_url == f"/console/incidents/{incident.pk}/?all=1"
    assert detail.recent_url == f"/console/incidents/{incident.pk}/"


@pytest.mark.django_db
def test_the_cap_does_not_change_the_query_count(monkeypatch):
    incident, _, _ = _long(7)
    with CaptureQueriesContext(connection) as whole:
        build_incident_detail(incident.pk)
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 4)

    with CaptureQueriesContext(connection) as capped:
        build_incident_detail(incident.pk)
    with CaptureQueriesContext(connection) as full:
        build_incident_detail(incident.pk, full=True)

    assert len(whole) == len(capped) == len(full) == 7
