import pytest

from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus
from apps.checkers.describing import check_status, describe_check, describe_check_errors
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
