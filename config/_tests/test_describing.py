import pytest

from config.describing import (
    Action,
    Description,
    Fact,
    facts_from,
    format_duration,
    format_list,
    format_percent,
    format_size_mb,
)


def test_description_defaults():
    d = Description(headline="CPU at 94%", tone="critical")
    assert (d.detail, d.action, d.facts) == ("", None, ())


def test_description_rejects_an_unknown_tone():
    with pytest.raises(ValueError, match="tone"):
        Description(headline="x", tone="red")


def test_description_rejects_an_empty_headline():
    with pytest.raises(ValueError, match="headline"):
        Description(headline="", tone="ok")


def test_action_and_fact_defaults():
    assert Action("Restart nginx") == Action(text="Restart nginx", intent="", params={})
    assert Fact("Host", "web-01").copy is False


@pytest.mark.parametrize(
    "value,expected", [(94, "94%"), (94.0, "94%"), (94.25, "94.2%"), (0, "0%"), (99.96, "100%")]
)
def test_format_percent(value, expected):
    assert format_percent(value) == expected


@pytest.mark.parametrize(
    "mb,expected",
    [
        (0, "0 MB"),
        (512.4, "512 MB"),
        (1024, "1.0 GB"),
        (17544.1, "17.1 GB"),
        (3 * 1024**2, "3.0 TB"),
    ],
)
def test_format_size_mb(mb, expected):
    assert format_size_mb(mb) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (-5, "0s"),
        (0, "0s"),
        (0.0004, "0ms"),
        (0.25, "250ms"),
        (0.85, "850ms"),
        (0.9996, "999ms"),
        (1, "1s"),
        (59, "59s"),
        (59.9, "59s"),
        (60, "1m 00s"),
        (61, "1m 01s"),
        (252, "4m 12s"),
        (3599, "59m 59s"),
        (3600, "1h 00m"),
        (3700, "1h 01m"),
        (11100, "3h 05m"),
        (86399, "23h 59m"),
        (86400, "1d 0h"),
        (90000, "1d 1h"),
        (187200, "2d 4h"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    "items,expected",
    [
        ([], ""),
        (["a"], "a"),
        (["a", "b"], "a and b"),
        (["a", "b", "c"], "a, b and c"),
        (["a", "b", "c", "d", "e"], "a, b, c and 2 more"),
    ],
)
def test_format_list(items, expected):
    assert format_list(items) == expected


def test_facts_from_keeps_scalars_and_skips_nested_values():
    facts = facts_from({"cpu_percent": 94.2, "per_cpu": [1, 2], "ok": True, "gone": None})
    assert facts == (Fact("Cpu percent", "94.2"), Fact("Ok", "True"))
