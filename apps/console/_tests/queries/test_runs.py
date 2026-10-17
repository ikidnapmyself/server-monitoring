import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.filters import RUN_STATUS_CHOICES, RunFilter
from apps.console.queries import runs as run_queries
from apps.console.queries.runs import build_run_list, origin_label
from apps.orchestration.describing import describe_run
from apps.orchestration.models import PipelineRun, PipelineStatus

pytestmark = pytest.mark.django_db

FROZEN = datetime(2026, 10, 17, 12, 0, tzinfo=dt_timezone.utc)


def _incident(**fields):
    return Incident.objects.create(severity="critical", status="open", subject="disk", **fields)


def _alert(incident, **fields):
    defaults = {"fingerprint": str(uuid.uuid4()), "name": "disk", "severity": "critical"}
    defaults |= {"status": "firing", "source": "cluster", "started_at": timezone.now()}
    return Alert.objects.create(incident=incident, **(defaults | fields))


def _run(status="notified", incident=None, **fields):
    return PipelineRun.objects.create(
        run_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        status=status,
        incident=incident,
        **fields,
    )


def _pks(listing):
    return {row.pk for row in listing.rows}


def _group(listing, name):
    return next(g for g in listing.groups if g.name == name)


def test_rows_are_newest_first_and_described():
    incident = _incident()
    old = _run(incident=incident)
    mid = _run(status="failed", last_error_message="boom")
    new = _run(status="checked", completed_at=timezone.now())
    now = timezone.now()
    for run, age in ((old, 3), (mid, 2), (new, 1)):
        PipelineRun.objects.filter(pk=run.pk).update(created_at=now - timedelta(hours=age))

    rows = build_run_list(RunFilter()).rows

    assert [row.pk for row in rows] == [new.pk, mid.pk, old.pk]
    for row, run in zip(rows, (new, mid, old), strict=True):
        run.refresh_from_db()
        assert row.what == describe_run(run)
        assert row.url == f"/console/runs/{run.pk}/"
        assert row.attempts == 1
        assert row.created_at == run.created_at
    assert rows[0].what.headline == "Run finished, diagnosed, nothing to notify"
    assert (rows[2].incident_pk, rows[2].incident_url) == (
        incident.pk,
        f"/console/incidents/{incident.pk}/",
    )
    assert (rows[0].incident_pk, rows[0].incident_url) == (None, "")


@pytest.mark.parametrize("key", list(RUN_STATUS_CHOICES))
def test_each_status_chip_selects_its_bucket(key):
    for status in PipelineStatus.values:
        _run(status=status)

    listing = build_run_list(RunFilter(status=key))

    expected = RUN_STATUS_CHOICES[key] or PipelineStatus.values
    assert listing.total == len(expected)
    assert {row.what.facts[2].value for row in listing.rows} == set(expected)
    counts = {chip.key: chip.count for chip in _group(listing, "status").chips}
    assert counts == {k: len(v or PipelineStatus.values) for k, v in RUN_STATUS_CHOICES.items()}
    active = [chip.key for chip in _group(listing, "status").chips if chip.active]
    assert active == [key]


def test_origin_and_since_narrow_the_rows(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)
    manual = _run(origin="manual")
    hook = _run(origin="incoming_webhook")
    stale = _run(origin="manual")
    PipelineRun.objects.filter(pk__in=[manual.pk, hook.pk]).update(
        created_at=FROZEN - timedelta(hours=1)
    )
    PipelineRun.objects.filter(pk=stale.pk).update(created_at=FROZEN - timedelta(days=2))

    assert _pks(build_run_list(RunFilter(origin="manual"))) == {manual.pk, stale.pk}
    assert _pks(build_run_list(RunFilter(since="24h"))) == {manual.pk, hook.pk}
    assert _pks(build_run_list(RunFilter(origin="manual", since="24h"))) == {manual.pk}
    origin = _group(build_run_list(RunFilter(origin="manual")), "origin")
    assert [chip.key for chip in origin.chips] == [
        "any",
        "incoming_webhook",
        "checker_generated",
        "manual",
    ]
    assert [chip.label for chip in origin.chips if chip.active] == ["Manual / CLI"]


def test_q_matches_run_id_trace_id_and_fingerprints():
    incident = _incident()
    _alert(incident, fingerprint="fp-on-incident")
    by_alert = _run(incident=incident)
    by_own = _run(alert_fingerprint="fp-own-run")
    other = _run(incident=_incident())
    _alert(other.incident, fingerprint="fp-elsewhere")

    assert _pks(build_run_list(RunFilter(q=by_alert.run_id[:13]))) == {by_alert.pk}
    assert _pks(build_run_list(RunFilter(q=by_own.trace_id))) == {by_own.pk}
    assert _pks(build_run_list(RunFilter(q="fp-own"))) == {by_own.pk}
    assert _pks(build_run_list(RunFilter(q="fp-on-incident"))) == {by_alert.pk}
    listing = build_run_list(RunFilter(q="fp-on-incident", status="failed"))
    assert listing.q == "fp-on-incident"
    assert listing.kept == (("status", "failed"),)


def test_a_node_matches_by_fk_and_through_the_incidents_alerts():
    web = Node.objects.create(instance_id="i-web", hostname="web")
    db = Node.objects.create(instance_id="i-db", hostname="db")
    a = _run(node=web)
    b_incident = _incident()
    _alert(b_incident, node=web)
    b = _run(incident=b_incident)
    c_incident = _incident()
    _alert(c_incident, labels={"instance_id": "i-web"})
    c = _run(incident=c_incident)
    d_incident = _incident()
    _alert(d_incident, node=web)
    _run(node=db, incident=d_incident)
    _run()

    listing = build_run_list(RunFilter(node=web.pk))

    assert _pks(listing) == {a.pk, b.pk, c.pk}
    node = _group(listing, "node")
    assert [chip.label for chip in node.chips] == ["Any node", "db", "web"]
    assert [chip.label for chip in node.chips if chip.active] == ["web"]


def test_a_row_names_its_node_or_its_incidents_hosts():
    web = Node.objects.create(instance_id="i-web", hostname="web")
    bare = Node.objects.create(instance_id="i-bare")
    hosts = _incident()
    _alert(hosts, labels={"instance_id": "i-a"})
    _alert(hosts, labels={"instance_id": "i-b"})
    linked = _run(node=web, incident=hosts)
    unnamed = _run(node=bare)
    unlinked = _run(incident=hosts)
    alone = _run()

    rows = {row.pk: row.node for row in build_run_list(RunFilter()).rows}

    assert rows == {linked.pk: "web", unnamed.pk: "i-bare", unlinked.pk: "i-a +1", alone.pk: ""}


def test_origin_and_stage_read_as_words():
    _run(origin="checker_generated", current_stage="check")
    _run(origin="cron_job", current_stage=None)

    rows = build_run_list(RunFilter()).rows

    assert {(row.origin, row.stage) for row in rows} == {
        ("Checker generated", "Diagnose"),
        ("Cron job", ""),
    }
    assert origin_label("manual") == "Manual / CLI"


def test_an_unknown_node_or_origin_is_dropped_with_a_notice():
    _run()

    listing = build_run_list(RunFilter(node=999))

    assert listing.notice == "Ignored invalid filter: node."
    assert listing.total == 1
    assert listing.url == "/console/runs/"
    dropped = build_run_list(RunFilter(dropped=("origin",)))
    assert dropped.notice == "Ignored invalid filter: origin."
    assert [g.name for g in dropped.groups] == ["status", "origin", "since"]


def test_pages_hold_page_size_rows_and_clamp(monkeypatch):
    monkeypatch.setattr(run_queries, "PAGE_SIZE", 2)
    for _ in range(5):
        _run()

    first = build_run_list(RunFilter())
    middle = build_run_list(RunFilter(page=2))
    last = build_run_list(RunFilter(page=9))

    assert (first.pages, len(first.rows), first.prev_url, first.next_url) == (
        3,
        2,
        "",
        "/console/runs/?page=2",
    )
    assert (middle.prev_url, middle.next_url) == ("/console/runs/", "/console/runs/?page=3")
    assert (last.page, len(last.rows), last.next_url) == (3, 1, "")
    assert last.url == "/console/runs/?page=3"


def test_empty_states():
    assert build_run_list(RunFilter()).empty == "none"
    _run(status="notified")
    listing = build_run_list(RunFilter(status="failed"))
    assert (listing.rows, listing.empty) == ((), "no_match")
    assert build_run_list(RunFilter()).empty == ""


def _seed(node, n):
    for index in range(n):
        incident = _incident()
        _alert(incident, labels={"instance_id": "i-web"})
        _run(incident=incident, node=None if index % 2 else node)


def _queries():
    with CaptureQueriesContext(connection) as captured:
        build_run_list(RunFilter())
    return len(captured)


def test_the_query_count_does_not_grow_with_the_rows():
    web = Node.objects.create(instance_id="i-web", hostname="web")
    _seed(web, 3)
    few = _queries()
    _seed(web, 27)

    assert _queries() == few
