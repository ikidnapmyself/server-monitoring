import inspect
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.alerts.describing import alert_name, describe_incident
from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.alerts.services import INCIDENT_ACTIONS
from apps.console._tests.queries.test_stages import CHECK_SNAPSHOT, NOTIFY_SNAPSHOT
from apps.console.queries import incident as incident_queries
from apps.console.queries.incident import build_incident_detail
from apps.console.queries.stages import CheckBody, RawBody
from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution
from config.describing import Action, Description

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=dt_timezone.utc)
QUERIES = 9


def _incident(created_at=T0, **fields):
    incident = Incident.objects.create(**fields)
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


@pytest.mark.django_db
def test_a_missing_incident_is_none():
    assert build_incident_detail(404) is None


@pytest.mark.django_db
def test_header_fields_describe_the_incident():
    pipeline = PipelineDefinition.objects.create(name="pager", stages=["check", "notify"])
    incident = _incident(
        subject="DiskFull",
        instance="web-1",
        severity="critical",
        status="acknowledged",
        summary="Logs rotated",
        pipeline=pipeline,
    )
    web2 = Node.objects.create(instance_id="id-2", hostname="web-2")
    bare = Node.objects.create(instance_id="Alpha")
    _alert(incident, web2, key="a")
    _alert(incident, web2, key="b")
    _alert(incident, bare, key="c")
    lead = _alert(incident, None, key="DiskFull", severity="critical")
    Alert.objects.filter(pk=lead.pk).update(description="92% used")

    detail = build_incident_detail(incident.pk)

    assert detail.pk == incident.pk
    assert detail.what == describe_incident(incident)
    assert (detail.what.headline, detail.what.detail) == ("DiskFull on web-1", "92% used")
    assert (detail.severity, detail.severity_tone) == ("critical", "critical")
    assert (detail.status, detail.status_tone) == ("acknowledged", "warning")
    assert detail.summary == "Logs rotated"
    assert detail.created_at == T0
    assert detail.nodes == "Alpha, web-2"
    assert detail.pipeline == "pager"
    assert detail.alert_count == 4
    assert detail.url == f"/console/incidents/{incident.pk}/"
    assert detail.full_url == f"/console/incidents/{incident.pk}/?all=1"
    assert detail.recent_url == ""
    assert detail.hidden == 0
    assert detail.do == ()
    assert isinstance(detail.stages, tuple)
    assert isinstance(detail.timeline, tuple)
    with pytest.raises(FrozenInstanceError):
        detail.what = None
    for gone in ("title", "description", "admin_url", "actions"):
        assert not hasattr(detail, gone)


def test_the_read_model_builds_no_admin_url():
    source = inspect.getsource(incident_queries)

    assert "admin:" not in source


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

    assert [(entry.id, entry.kind, entry.what, entry.when) for entry in detail.timeline] == [
        ("entry-incident-created", "incident", Description("Incident opened", "critical"), T0),
        (
            "entry-incident-acknowledged",
            "incident",
            Description("Incident acknowledged", "warning"),
            T0 + timedelta(minutes=1),
        ),
        (
            "entry-incident-resolved",
            "incident",
            Description("Incident resolved", "ok"),
            T0 + timedelta(minutes=2),
        ),
        (
            "entry-incident-closed",
            "incident",
            Description("Incident closed", "muted"),
            T0 + timedelta(minutes=3),
        ),
    ]
    assert all(entry.body is None for entry in detail.timeline)


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
def test_alert_history_entries_use_the_alert_describer_and_link_the_alert():
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

    link = Action("Open the alert", intent="view_alert", params={"pk": alert.pk})
    entry = _entry(detail, f"entry-alert_history-{created.pk}")
    assert entry.kind == "alert_history"
    assert entry.what.headline == "cpu high started firing"
    assert entry.what.tone == "info"
    assert entry.what.action == link
    assert [(f.label, f.value) for f in entry.what.facts] == [("Status", "firing")]
    assert entry.body is None
    entry = _entry(detail, f"entry-alert_history-{changed.pk}")
    assert entry.what.headline == "cpu high moved from warning to critical"
    assert entry.what.tone == "critical"
    entry = _entry(detail, f"entry-alert_history-{resolved.pk}")
    assert entry.what.headline == "cpu high cleared"
    assert entry.what.tone == "ok"
    assert entry.what.facts == ()


@pytest.mark.django_db
def test_a_checker_history_headline_holds_when_the_alerts_metrics_change():
    incident = _incident(instance="web-1")
    alert = Alert.objects.create(
        fingerprint="check:web-1:cpu",
        name="cpu",
        severity="critical",
        source="cluster",
        labels={"checker": "cpu"},
        raw_payload={"metrics": {"cpu_percent": 97.0}},
        incident=incident,
        started_at=T0,
    )
    row = _history(alert, "created", T0)
    entry_id = f"entry-alert_history-{row.pk}"

    before = _entry(build_incident_detail(incident.pk), entry_id).what.headline
    Alert.objects.filter(pk=alert.pk).update(raw_payload={"metrics": {"cpu_percent": 12.0}})
    after = _entry(build_incident_detail(incident.pk), entry_id).what.headline

    assert before == after == f"{alert_name(alert)} started firing"
    assert "97" not in before


@pytest.mark.django_db
def test_run_and_stage_entries_use_the_orchestration_describers():
    incident = _incident()
    run = _run(incident, T0 + timedelta(seconds=1), status="failed", run_id="run-9")
    PipelineRun.objects.filter(pk=run.pk).update(last_error_message="no_route: nothing matched")
    check = _stage(
        run, "check", T0 + timedelta(seconds=2), duration_ms=850.0, output_snapshot=CHECK_SNAPSHOT
    )
    notify = _stage(
        run,
        "notify",
        T0 + timedelta(seconds=3),
        status="failed",
        error_message="slack 500",
        error_retryable=False,
    )
    routed = _stage(
        run,
        "notify",
        T0 + timedelta(seconds=4),
        status="failed",
        attempt=2,
        error_message="no_channel: the lane names no channel",
    )
    odd = _stage(run, "custom", T0 + timedelta(seconds=5), status="running")

    detail = build_incident_detail(incident.pk)

    open_run = Action("Open the run", intent="view_run", params={"pk": run.pk})
    entry = _entry(detail, f"entry-pipeline-{run.pk}")
    assert entry.kind == "pipeline"
    assert entry.what.headline == "Run failed: no route matches this incident"
    assert entry.what.action.intent == "edit_route"
    assert ("Run", "run-9") in [(f.label, f.value) for f in entry.what.facts]
    assert entry.body is None
    entry = _entry(detail, f"entry-stage-{check.pk}")
    assert entry.kind == "stage"
    assert (entry.what.headline, entry.what.tone) == ("Diagnose finished in 850ms", "ok")
    assert entry.what.action == open_run
    assert isinstance(entry.body, CheckBody)
    entry = _entry(detail, f"entry-stage-{notify.pk}")
    assert (entry.what.headline, entry.what.tone) == ("Notify failed", "critical")
    assert ("Error", "slack 500") in [(f.label, f.value) for f in entry.what.facts]
    assert entry.what.action.text == "Open the run's error in Details"
    assert entry.body is None
    entry = _entry(detail, f"entry-stage-{routed.pk}")
    assert entry.what.headline == "Not delivered: the matched route has no active channel"
    assert entry.what.action.intent == "edit_route"
    entry = _entry(detail, f"entry-stage-{odd.pk}")
    assert (entry.what.headline, entry.what.tone) == ("Custom is running", "info")
    assert entry.what.action == open_run


@pytest.mark.django_db
def test_a_quiet_run_links_itself():
    incident = _incident()
    run = _run(incident, T0)

    entry = _entry(build_incident_detail(incident.pk), f"entry-pipeline-{run.pk}")

    assert (entry.what.headline, entry.what.tone) == ("Run finished, notified", "ok")
    assert entry.what.action == Action("Open the run", intent="view_run", params={"pk": run.pk})


@pytest.mark.django_db
def test_stage_summaries_follow_the_diagnosis():
    pipeline = PipelineDefinition.objects.create(name="no-ai", stages=["check", "notify"])
    incident = _incident(pipeline=pipeline)
    run = _run(incident, T0)
    _stage(run, "check", T0, output_snapshot=CHECK_SNAPSHOT)

    detail = build_incident_detail(incident.pk)

    open_run = Action("Open the run", intent="view_run", params={"pk": run.pk})
    assert [(s.stage, s.label) for s in detail.stages] == [
        ("check", "Diagnose"),
        ("analyze", "Analyze"),
        ("notify", "Notify"),
    ]
    check, analyze, notify = (s.outcome for s in detail.stages)
    assert (check.headline, check.tone, check.action) == ("Diagnose finished", "ok", open_run)
    assert [(f.label, f.value) for f in check.facts] == [("Runs", "succeeded in 1/1 runs")]
    assert (analyze.headline, analyze.tone) == ("Analyze skipped", "muted")
    assert analyze.detail == "not in this route's stages"
    assert (analyze.action, analyze.facts) == (None, ())
    assert (notify.headline, notify.tone) == ("Notify hasn't run yet", "muted")
    assert [(f.label, f.value) for f in notify.facts] == [("Runs", "succeeded in 0/1 runs")]
    assert notify.action is None


@pytest.mark.django_db
def test_a_legacy_run_adds_the_ingest_summary():
    incident = _incident()
    run = PipelineRun.objects.create(trace_id="t", run_id="legacy", incident=incident)
    _stage(run, "ingest", T0, status="running")
    _stage(
        run,
        "check",
        T0,
        status="failed",
        error_type="Boom",
        error_message="no_route: x",
        error_retryable=False,
    )
    _stage(run, "analyze", T0, status="succeeded")

    detail = build_incident_detail(incident.pk)

    assert [(s.label, s.outcome.headline, s.outcome.tone) for s in detail.stages] == [
        ("Ingest", "Ingest stalled", "warning"),
        ("Diagnose", "Diagnose failed: no route matches this incident", "critical"),
        ("Analyze", "Analyze finished with nothing to report", "warning"),
        ("Notify", "Notify hasn't run yet", "muted"),
    ]
    assert detail.stages[1].outcome.action.intent == "edit_route"


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
    assert len(few) == len(many) == QUERIES


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

    assert len(whole) == len(capped) == len(full) == QUERIES


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["open", "acknowledged", "resolved", "closed"])
def test_nothing_to_do_unless_the_caller_may_act(status):
    incident = _incident(status=status)

    assert build_incident_detail(incident.pk).do == ()
    assert build_incident_detail(incident.pk, can_act=False).do == ()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "keys"),
    [
        ("open", ("acknowledge", "resolve", "close")),
        ("acknowledged", ("resolve", "close")),
        ("resolved", ("close",)),
        ("closed", ()),
    ],
)
def test_transitions_follow_the_status_when_the_caller_may_act(status, keys):
    incident = _incident(status=status)

    detail = build_incident_detail(incident.pk, can_act=True)

    assert detail.do == tuple(
        Action(key.capitalize(), intent=key, params={"pk": incident.pk}) for key in keys
    )


@pytest.mark.django_db
@override_settings(INSTANCE_ID="this-box")
@pytest.mark.parametrize(
    ("instance", "checker", "can_rerun", "offered"),
    [
        ("this-box", "cpu", True, True),
        ("this-box", "cpu", False, False),
        ("other-box", "cpu", True, False),
        ("this-box", "no_such_checker", True, False),
    ],
)
def test_rerun_is_offered_only_for_a_local_incident_with_a_known_checker(
    instance, checker, can_rerun, offered
):
    incident = _incident(instance=instance, status="closed")
    Alert.objects.create(
        fingerprint=f"check:{instance}:{checker}",
        name=checker,
        source="cluster",
        labels={"checker": checker},
        incident=incident,
        started_at=T0,
    )

    detail = build_incident_detail(incident.pk, can_act=True, can_rerun=can_rerun)

    rerun = Action("Run the checks again", intent="rerun_check", params={"pk": incident.pk})
    assert detail.do == ((rerun,) if offered else ())


@pytest.mark.django_db
@override_settings(INSTANCE_ID="this-box")
def test_rerun_follows_the_transitions():
    incident = _incident(instance="this-box", status="resolved")
    _alert(incident, key="x")
    Alert.objects.filter(incident=incident).update(labels={"checker": "cpu"})

    detail = build_incident_detail(incident.pk, can_act=True, can_rerun=True)

    assert [a.intent for a in detail.do] == ["close", "rerun_check"]


@pytest.mark.django_db
def test_the_detail_says_whether_it_is_the_full_timeline():
    incident = _incident()

    assert build_incident_detail(incident.pk).full is False
    assert build_incident_detail(incident.pk, full=True).full is True


def test_every_incident_action_has_a_label():
    assert set(incident_queries.ACTION_LABELS) == set(INCIDENT_ACTIONS)
