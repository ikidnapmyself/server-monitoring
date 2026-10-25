from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.models import Node
from apps.checkers.models import PreflightCheck, PreflightRun
from apps.checkers.preflight_describing import describe_preflight_run
from apps.console.filters import PreflightFilter
from apps.console.queries import preflight as preflight_queries
from apps.console.queries.preflight import build_preflight_list, trigger_label
from apps.console.queries.preflight_run import build_preflight_detail
from config.describing import Action, Description, Fact

pytestmark = pytest.mark.django_db


def _preflight(status="ok", checks=(), **fields):
    defaults = {"instance_id": "i-hub", "triggered_by": "cli", "passed": 3}
    run = PreflightRun.objects.create(overall_status=status, **(defaults | fields))
    for level, message, hint in checks:
        PreflightCheck.objects.create(run=run, level=level, message=message, hint=hint)
    return run


def _aged(run, hours):
    PreflightRun.objects.filter(pk=run.pk).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )
    run.refresh_from_db()
    return run


def _group(listing, name):
    return next(g for g in listing.groups if g.name == name)


def test_rows_are_newest_first_and_described():
    Node.objects.create(instance_id="i-hub", hostname="hub")
    old = _aged(_preflight(), 3)
    new = _aged(_preflight("error", errors=2, instance_id="i-gone", triggered_by=""), 1)

    rows = build_preflight_list(PreflightFilter()).rows

    assert [row.pk for row in rows] == [new.pk, old.pk]
    assert rows[0].what == describe_preflight_run(new)
    assert rows[0].what.headline == "Preflight found 2 errors"
    assert rows[0].link == Action("Preflight found 2 errors", "view_preflight_run", {"pk": new.pk})
    assert (rows[0].trigger, rows[0].node) == ("", "")
    assert (rows[1].trigger, rows[1].node) == ("From the command line", "hub")
    assert rows[1].created_at == old.created_at


@pytest.mark.parametrize(
    ("stored", "label"),
    [("cli", "From the command line"), ("check_health", "Check health"), ("", "")],
)
def test_trigger_label(stored, label):
    assert trigger_label(stored) == label


def test_each_filter_narrows_the_list():
    ok = _preflight()
    warn = _preflight("warn", warnings=1)
    error = _aged(_preflight("error", errors=1), 48)

    def picked(**fields):
        return {row.pk for row in build_preflight_list(PreflightFilter(**fields)).rows}

    assert picked() == {ok.pk, warn.pk, error.pk}
    assert picked(status="error") == {error.pk}
    assert picked(status="warn") == {warn.pk}
    assert picked(status="ok") == {ok.pk}
    assert picked(since="24h") == {ok.pk, warn.pk}


def test_status_chips_count_and_carry_tones():
    _preflight()
    _preflight("error", errors=1)
    _aged(_preflight("warn", warnings=1), 48)

    listing = build_preflight_list(PreflightFilter(since="24h"))

    chips = {chip.key: chip for chip in _group(listing, "status").chips}
    assert {key: chip.count for key, chip in chips.items()} == {
        "error": 1,
        "warn": 0,
        "ok": 1,
        "all": 2,
    }
    assert {key: chip.tone for key, chip in chips.items()} == {
        "error": "critical",
        "warn": "warning",
        "ok": "ok",
        "all": "",
    }
    assert chips["all"].active
    assert chips["error"].url == "/console/preflight/?status=error&since=24h"


def test_empty_and_notice():
    assert build_preflight_list(PreflightFilter()).empty == "none"
    _preflight()
    assert build_preflight_list(PreflightFilter(status="error")).empty == "no_match"
    listing = build_preflight_list(PreflightFilter(dropped=("status",)))
    assert listing.notice == "Ignored invalid filter: status."
    assert listing.empty == ""
    assert listing.url == "/console/preflight/"


def test_pages_clamp_and_link(monkeypatch):
    monkeypatch.setattr(preflight_queries, "PAGE_SIZE", 2)
    for _ in range(5):
        _preflight()

    first = build_preflight_list(PreflightFilter(status="ok"))
    last = build_preflight_list(PreflightFilter(status="ok", page=9))

    assert (first.page, first.pages, len(first.rows), first.prev_url) == (1, 3, 2, "")
    assert first.next_url == "/console/preflight/?status=ok&page=2"
    assert (last.page, len(last.rows), last.next_url) == (3, 1, "")
    assert last.prev_url == "/console/preflight/?status=ok&page=2"


def test_a_missing_preflight_run_is_none():
    assert build_preflight_detail(404) is None


def test_the_detail_groups_checks_by_level_errors_first():
    node = Node.objects.create(instance_id="i-hub", hostname="hub")
    run = _preflight(
        "error",
        errors=1,
        warnings=1,
        passed=2,
        checks=[
            ("ok", "Python 3.12", ""),
            ("warn", "DEBUG is on", "Set DEBUG=0"),
            ("odd", "Strange level", ""),
            ("error", ".env is missing", "Copy .env.sample"),
            ("info", "3 migrations applied", ""),
            ("ok", "Database reachable", ""),
        ],
    )

    detail = build_preflight_detail(run.pk)

    assert [(g.key, g.label) for g in detail.groups] == [
        ("error", "Errors"),
        ("warn", "Warnings"),
        ("info", "Info"),
        ("ok", "Passed"),
        ("other", "Other"),
    ]
    assert detail.groups[0].checks == (
        Description(".env is missing", "critical", detail="Copy .env.sample"),
    )
    assert [c.headline for c in detail.groups[3].checks] == ["Python 3.12", "Database reachable"]
    assert detail.groups[4].checks == (Description("Strange level", "muted"),)
    assert detail.what.headline == "Preflight found 1 error"
    assert detail.what.facts == (
        Fact("Status", "error"),
        Fact("Instance ID", "i-hub", copy=True),
        Fact("Triggered by", "cli"),
    )
    assert detail.node == Action("hub", "view_node", {"pk": node.pk})
    assert detail.trigger == "From the command line"
    assert detail.counts == "2 passed, 1 warning, 1 error"
    assert detail.created_at == run.created_at


def test_a_bare_run_leaves_out_empty_groups_and_unknown_facts():
    run = _preflight(instance_id="", triggered_by="", passed=1)

    detail = build_preflight_detail(run.pk)

    assert detail.groups == ()
    assert detail.node is None
    assert detail.what.facts == (Fact("Status", "ok"),)
    assert detail.counts == "1 passed, 0 warnings, 0 errors"
