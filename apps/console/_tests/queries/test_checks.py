from datetime import timedelta

import pytest
from django.utils import timezone

from apps.checkers.checkers import CHECKER_REGISTRY
from apps.checkers.models import CheckRun, CheckStatus
from apps.console.filters import CheckFilter
from apps.console.queries import checks as check_queries
from apps.console.queries.checks import (
    build_check_list,
    checker_choices,
    describe_check_run,
    host_choices,
)
from config.describing import Action

pytestmark = pytest.mark.django_db


def _check(checker="cpu", status="ok", **fields):
    defaults = {"hostname": "web", "message": "CPU at 12%", "metrics": {"cpu_percent": 12.0}}
    return CheckRun.objects.create(checker_name=checker, status=status, **(defaults | fields))


def _aged(run, hours):
    CheckRun.objects.filter(pk=run.pk).update(executed_at=timezone.now() - timedelta(hours=hours))
    run.refresh_from_db()
    return run


def _pks(listing):
    return [row.pk for row in listing.rows]


def _group(listing, name):
    return next(g for g in listing.groups if g.name == name)


def test_rows_are_newest_first_and_described_by_the_checker():
    old = _aged(_check(duration_ms=1500), 3)
    new = _aged(_check("memory", "critical", metrics={"memory_percent": 97.0}), 1)

    rows = build_check_list(CheckFilter()).rows

    assert [row.pk for row in rows] == [new.pk, old.pk]
    assert rows[0].what == describe_check_run(new)
    assert (
        rows[0].what.headline
        == CHECKER_REGISTRY["memory"]()
        .describe(CheckStatus.CRITICAL, {"memory_percent": 97.0}, new.message)
        .headline
    )
    assert rows[0].link == Action(rows[0].what.headline, "view_check_run", {"pk": new.pk})
    assert rows[1].checker == CHECKER_REGISTRY["cpu"].label
    assert rows[1].host == "web"
    assert rows[1].took == "1s"
    assert rows[1].executed_at == old.executed_at


def test_an_unknown_checker_is_described_by_the_node():
    run = _check("gone_checker", "warning", message="Something odd\nmore lines")

    row = build_check_list(CheckFilter()).rows[0]

    assert row.what.headline == "Something odd"
    assert row.what.detail == "Something odd\nmore lines"
    assert ("Described by", "the node") in [(f.label, f.value) for f in row.what.facts]
    assert row.checker == "Gone checker"
    assert row.pk == run.pk


def test_metrics_that_arent_a_dict_read_as_none():
    run = _check(metrics=[1, 2])

    assert describe_check_run(run) == describe_check_run(_check(metrics={}))


def test_each_filter_narrows_the_list():
    ok = _check()
    critical = _check(status="critical", hostname="db")
    warning = _check("disk", "warning", message="Disk at 85%")
    skipped = _check(status="skipped")
    old = _aged(_check(status="unknown"), 48)

    def picked(**fields):
        return set(_pks(build_check_list(CheckFilter(**fields))))

    assert picked() == {ok.pk, critical.pk, warning.pk, skipped.pk, old.pk}
    assert picked(status="failing") == {critical.pk, warning.pk, old.pk}
    assert picked(status="ok") == {ok.pk}
    assert picked(checker="disk") == {warning.pk}
    assert picked(host="db") == {critical.pk}
    assert picked(since="24h") == {ok.pk, critical.pk, warning.pk, skipped.pk}
    assert picked(q="disk at") == {warning.pk}
    assert picked(q="DB") == {critical.pk}


def test_status_chips_count_the_other_filters_and_carry_them():
    _check()
    _check(status="critical")
    _check(status="warning", hostname="db")

    listing = build_check_list(CheckFilter(host="web"))

    chips = {chip.key: chip for chip in _group(listing, "status").chips}
    assert {key: chip.count for key, chip in chips.items()} == {
        "failing": 1,
        "critical": 1,
        "warning": 0,
        "unknown": 0,
        "ok": 1,
        "skipped": 0,
        "all": 2,
    }
    assert chips["all"].active
    assert chips["failing"].url == "/console/checks/?status=failing&host=web"
    assert [chip.key for chip in _group(listing, "since").chips] == ["any", "24h", "7d", "30d"]


def test_the_checker_and_host_choices():
    _check("gone_checker", hostname="Zed")
    _check(hostname="")
    _check(hostname="alpha")

    checkers = checker_choices()
    assert ("gone_checker", "Gone checker") in checkers
    assert {key for key, _ in checkers} == {*CHECKER_REGISTRY, "gone_checker"}
    assert [label for _, label in checkers] == sorted(label for _, label in checkers)
    assert host_choices("") == ("alpha", "Zed")
    assert host_choices("typed") == ("alpha", "typed", "Zed")


def test_the_list_carries_its_filters_back():
    listing = build_check_list(
        CheckFilter(q="x", status="ok", checker="cpu", host="web", since="7d", dropped=("page",))
    )

    assert (listing.q, listing.checker, listing.host) == ("x", "cpu", "web")
    assert listing.kept == (("status", "ok"), ("since", "7d"))
    assert listing.notice == "Ignored invalid filter: page."
    assert listing.url == "/console/checks/?q=x&status=ok&checker=cpu&host=web&since=7d"


def test_empty_says_whether_anything_exists():
    assert build_check_list(CheckFilter()).empty == "none"
    _check()
    assert build_check_list(CheckFilter(status="critical")).empty == "no_match"
    assert build_check_list(CheckFilter()).empty == ""


def test_pages_clamp_and_link(monkeypatch):
    monkeypatch.setattr(check_queries, "PAGE_SIZE", 2)
    for _ in range(5):
        _check()

    first = build_check_list(CheckFilter(status="ok"))
    last = build_check_list(CheckFilter(status="ok", page=9))

    assert (first.page, first.pages, len(first.rows)) == (1, 3, 2)
    assert first.prev_url == ""
    assert first.next_url == "/console/checks/?status=ok&page=2"
    assert (last.page, len(last.rows)) == (3, 1)
    assert last.prev_url == "/console/checks/?status=ok&page=2"
    assert last.next_url == ""
