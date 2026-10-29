from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.models import Node
from apps.console._tests.queries.test_runs import _alert, _incident, _run
from apps.console.filters import InboxFilter
from apps.console.queries import inbox as inbox_queries
from apps.console.queries.inbox import build_inbox
from apps.orchestration.describing import describe_run
from apps.orchestration.inbox import DEFAULT_STALE_MINUTES
from apps.orchestration.models import PipelineRun
from config.describing import Action

pytestmark = pytest.mark.django_db

DRAIN = Action("Drain the oldest runs", "drain_inbox")
RECLAIM = Action("Return stuck runs to the queue", "reclaim_inbox")


def _aged(run, minutes, field="updated_at"):
    PipelineRun.objects.filter(pk=run.pk).update(
        **{field: timezone.now() - timedelta(minutes=minutes)}
    )
    return PipelineRun.objects.get(pk=run.pk)


def _pks(inbox):
    return [row.pk for row in inbox.rows]


def test_rows_are_pending_and_processing_oldest_first():
    new = _run(status="pending")
    old = _aged(_run(status="processing"), 5, "created_at")
    for status in ("notified", "failed", "retrying", "skipped"):
        _run(status=status)

    inbox = build_inbox(InboxFilter())

    assert _pks(inbox) == [old.pk, new.pk]
    assert (inbox.pending, inbox.processing, inbox.hidden, inbox.empty) == (1, 1, 0, "")
    assert inbox.url == "/console/inbox/"
    assert inbox.kept == ()


def test_a_row_carries_its_description_node_origin_and_incident():
    incident = _incident()
    _alert(incident, labels={"instance_id": "web-1"})
    node = Node.objects.create(instance_id="i-1", hostname="db-1")
    linked = _run(status="pending", node=node, origin="incoming_webhook")
    unlinked = _run(status="pending", incident=incident, origin="checker_generated")
    loose = _run(status="pending")

    rows = {row.pk: row for row in build_inbox(InboxFilter()).rows}

    assert rows[linked.pk].what == describe_run(linked)
    assert rows[linked.pk].node == "db-1"
    assert rows[linked.pk].incident_url == ""
    assert rows[unlinked.pk].incident_pk == incident.pk
    assert rows[unlinked.pk].incident_url == f"/console/incidents/{incident.pk}/"
    assert rows[unlinked.pk].url == f"/console/runs/{unlinked.pk}/"
    assert rows[unlinked.pk].since == PipelineRun.objects.get(pk=unlinked.pk).updated_at
    assert rows[unlinked.pk].node == "web-1"
    assert rows[loose.pk].node == ""
    assert (rows[linked.pk].origin, rows[unlinked.pk].origin) == (
        "Incoming webhook",
        "Checker generated",
    )


@pytest.mark.parametrize(("status", "shown"), [("pending", "pending"), ("processing", "busy")])
def test_the_status_filter_narrows_to_one_kind(status, shown):
    runs = {"pending": _run(status="pending"), "busy": _run(status="processing")}

    inbox = build_inbox(InboxFilter(status=status))

    assert _pks(inbox) == [runs[shown].pk]
    assert inbox.kept == (("status", status),)
    assert inbox.url == f"/console/inbox/?status={status}"
    assert (inbox.pending, inbox.processing) == (1, 1)


def test_a_processing_run_past_the_stale_window_is_stuck():
    stuck = _aged(_run(status="processing"), DEFAULT_STALE_MINUTES + 1)
    fresh = _aged(_run(status="processing"), DEFAULT_STALE_MINUTES - 1)
    waiting = _aged(_run(status="pending"), DEFAULT_STALE_MINUTES * 10)

    inbox = build_inbox(InboxFilter())

    assert {row.pk: row.stuck for row in inbox.rows} == {
        stuck.pk: True,
        fresh.pk: False,
        waiting.pk: False,
    }
    assert inbox.stuck == 1


@pytest.mark.parametrize("status", ["ingested", "checked", "analyzed", "retrying"])
def test_a_run_a_killed_drain_left_mid_lane_is_listed_and_stuck(status):
    stranded = _aged(_run(status=status), DEFAULT_STALE_MINUTES + 1)
    _run(status=status)
    _aged(_run(status=status, completed_at=timezone.now()), DEFAULT_STALE_MINUTES + 1)

    inbox = build_inbox(InboxFilter())

    assert [(row.pk, row.stuck) for row in inbox.rows] == [(stranded.pk, True)]
    assert (inbox.stuck, inbox.pending, inbox.processing) == (1, 0, 0)
    assert build_inbox(InboxFilter()).do == (RECLAIM,)


def test_the_stuck_filter_shows_every_stuck_run():
    busy = _aged(_run(status="processing"), DEFAULT_STALE_MINUTES + 1)
    checked = _aged(_run(status="checked"), DEFAULT_STALE_MINUTES + 1)
    _run(status="processing")
    _run(status="pending")

    inbox = build_inbox(InboxFilter(status="stuck"))

    assert _pks(inbox) == [busy.pk, checked.pk]
    assert inbox.url == "/console/inbox/?status=stuck"


def test_counts_cover_the_whole_queue_not_the_shown_rows(monkeypatch):
    monkeypatch.setattr(inbox_queries, "INBOX_LIMIT", 2)
    for _ in range(3):
        _run(status="pending")

    inbox = build_inbox(InboxFilter())

    assert len(inbox.rows) == 2
    assert (inbox.hidden, inbox.pending) == (1, 3)


def test_drain_now_is_offered_on_pending_rows():
    pending = _run(status="pending")
    busy = _run(status="processing")

    rows = {row.pk: row for row in build_inbox(InboxFilter()).rows}

    assert rows[pending.pk].drain == Action("Drain now", "drain_run", {"pk": pending.pk})
    assert rows[busy.pk].drain is None


def test_page_actions_drain_the_oldest_and_reclaim_only_when_stuck():
    assert build_inbox(InboxFilter()).do == ()
    _run(status="pending")
    assert build_inbox(InboxFilter()).do == (DRAIN,)
    _aged(_run(status="processing"), DEFAULT_STALE_MINUTES + 1)
    assert build_inbox(InboxFilter()).do == (DRAIN, RECLAIM)


def test_reclaim_is_offered_without_anything_pending():
    _aged(_run(status="processing"), DEFAULT_STALE_MINUTES + 1)

    assert build_inbox(InboxFilter()).do == (RECLAIM,)


def test_the_status_chips_count_the_queue():
    _run(status="pending")
    _run(status="pending")
    _run(status="processing")
    _aged(_run(status="processing"), DEFAULT_STALE_MINUTES + 1)
    _aged(_run(status="analyzed"), DEFAULT_STALE_MINUTES + 1)

    inbox = build_inbox(InboxFilter(status="pending"))

    (group,) = inbox.groups
    assert group.name == "status"
    assert [(c.key, c.count, c.active) for c in group.chips] == [
        ("both", 5, False),
        ("pending", 2, True),
        ("processing", 2, False),
        ("stuck", 2, False),
    ]
    assert [c.url for c in group.chips] == [
        "/console/inbox/",
        "/console/inbox/?status=pending",
        "/console/inbox/?status=processing",
        "/console/inbox/?status=stuck",
    ]


def test_an_empty_queue_says_so():
    _run(status="notified")

    inbox = build_inbox(InboxFilter())

    assert (inbox.rows, inbox.empty) == ((), "drained")


def test_a_filter_that_hides_the_queue_is_not_drained():
    _run(status="pending")

    inbox = build_inbox(InboxFilter(status="processing"))

    assert (inbox.rows, inbox.empty) == ((), "no_match")


def test_an_invalid_filter_is_named():
    inbox = build_inbox(InboxFilter(dropped=("status",)))

    assert inbox.notice == "Ignored invalid filter: status."
    assert build_inbox(InboxFilter()).notice == ""
