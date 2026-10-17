import pytest

from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus
from apps.checkers.describing import (
    PROBLEM_ORDER,
    check_status,
    describe_check,
    describe_check_errors,
    describe_checks,
    describe_findings,
)
from config.describing import Description, Fact


@pytest.mark.parametrize(
    "value, status",
    [
        ("ok", CheckStatus.OK),
        ("warning", CheckStatus.WARNING),
        ("critical", CheckStatus.CRITICAL),
        ("unknown", CheckStatus.UNKNOWN),
        ("skipped", CheckStatus.SKIPPED),
        ("bogus", CheckStatus.UNKNOWN),
        ("", CheckStatus.UNKNOWN),
        (None, CheckStatus.UNKNOWN),
        (["ok"], CheckStatus.UNKNOWN),
        ({"a": 1}, CheckStatus.UNKNOWN),
    ],
)
def test_check_status_reads_anything(value, status):
    assert check_status(value) is status


def test_a_known_checker_describes_its_own_result():
    metrics = {"cpu_percent": 97.0}
    expected = CHECKER_REGISTRY["cpu"]().describe(CheckStatus.CRITICAL, metrics, "msg")

    assert describe_check("cpu", CheckStatus.CRITICAL, metrics, "msg") == expected


def test_an_unknown_checker_falls_back_to_the_nodes_message():
    d = describe_check("gpu", CheckStatus.CRITICAL, {}, "  GPU on fire\nfan stopped  ")

    assert d == Description(
        "GPU on fire",
        "critical",
        detail="GPU on fire\nfan stopped",
        facts=(Fact("Described by", "the node"),),
    )


def test_a_one_line_message_is_not_repeated_as_detail():
    d = describe_check("gpu", CheckStatus.WARNING, {}, "GPU warm")

    assert (d.headline, d.detail, d.tone) == ("GPU warm", "", "warning")


@pytest.mark.parametrize("name, headline", [("zfs_pool", "Zfs pool"), ("", "Unnamed check")])
def test_an_unknown_checker_without_a_message_is_named(name, headline):
    assert describe_check(name, CheckStatus.UNKNOWN, {}, "").headline == headline


def test_no_errors_is_nothing():
    assert describe_check_errors(()) is None


@pytest.mark.parametrize(
    "errors, headline",
    [(("a",), "The checks hit 1 error"), (("a", "b"), "The checks hit 2 errors")],
)
def test_errors_are_counted_and_listed(errors, headline):
    d = describe_check_errors(errors)

    assert (d.headline, d.tone) == (headline, "warning")
    assert d.facts == tuple(Fact("Error", e) for e in errors)


def _snapshot(*checks):
    return {"checks": list(checks)}


def test_findings_are_problems_worst_first():
    snapshot = _snapshot(
        {"name": "cpu", "status": "warning", "metrics": {"cpu_percent": 85.0}, "message": "x"},
        {"name": "memory", "status": "ok", "metrics": {}, "message": "fine"},
        {"name": "nope", "status": "unknown", "metrics": {}, "message": "lost"},
        {"name": "disk", "status": "critical", "metrics": {}, "message": "full"},
    )

    found = describe_findings(snapshot)

    assert [d.tone for d in found] == ["critical", "muted", "warning"]


def test_findings_skip_ok_and_skipped():
    snapshot = _snapshot(
        {"name": "cpu", "status": "ok", "metrics": {}, "message": ""},
        {"name": "raid", "status": "skipped", "metrics": {}, "message": "no RAID"},
    )

    assert describe_findings(snapshot) == ()


def test_checks_group_by_status():
    found = describe_checks(_snapshot({"name": "cpu", "status": "ok", "metrics": {}}))

    assert len(found[CheckStatus.OK]) == 1
    assert set(found) == set(CheckStatus)


@pytest.mark.parametrize("snapshot", [None, "x", {}, {"checks": "nope"}])
def test_findings_read_any_shape(snapshot):
    assert describe_findings(snapshot) == ()


def test_findings_skip_entries_that_are_not_results():
    found = describe_findings(
        {"checks": ["nope", {"name": 7, "status": "critical", "metrics": [], "message": 1.5}]}
    )

    assert len(found) == 1
    assert found[0].headline == "1.5"


def test_problem_order():
    assert PROBLEM_ORDER == (CheckStatus.CRITICAL, CheckStatus.UNKNOWN, CheckStatus.WARNING)
