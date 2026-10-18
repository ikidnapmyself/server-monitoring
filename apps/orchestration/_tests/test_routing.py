import pytest

from apps.orchestration.routing import (
    describe_condition,
    describe_match,
    representable,
    split_field,
    value_words,
)


def test_split_field_label():
    assert split_field("label:team") == ("label", "team")


def test_split_field_plain():
    assert split_field("source") == ("source", "")


def test_value_words_choice_label():
    assert value_words("severity", "critical") == "Critical"


def test_value_words_value_not_in_choices_passes_through():
    assert value_words("severity", "catastrophic") == "catastrophic"


def test_value_words_kind_without_choices_passes_through():
    assert value_words("source", "grafana") == "grafana"


@pytest.mark.parametrize(
    "cond, words",
    [
        ({"field": "source", "op": "is", "value": "grafana"}, "Source is grafana"),
        ({"field": "severity", "op": "is", "value": "critical"}, "Severity is Critical"),
        ({"field": "status", "op": "is", "value": "resolved"}, "Status is Resolved"),
        ({"field": "instance", "op": "is", "value": "web-1"}, "Host is web-1"),
        (
            {"field": "origin", "op": "is", "value": "incoming_webhook"},
            "Started by is Incoming webhook",
        ),
        (
            {"field": "label:team", "op": "in", "value": ["web", "db"]},
            "Label team is one of web, db",
        ),
    ],
)
def test_describe_condition_every_field(cond, words):
    assert describe_condition(cond) == words


@pytest.mark.parametrize(
    "op, value, words",
    [
        ("is", "warning", "Severity is Warning"),
        ("is-not", "info", "Severity is not Info"),
        ("in", ["critical", "warning"], "Severity is one of Critical, Warning"),
        ("not-in", ["info"], "Severity is not one of Info"),
    ],
)
def test_describe_condition_every_op(op, value, words):
    assert describe_condition({"field": "severity", "op": op, "value": value}) == words


def test_describe_condition_missing_op_reads_as_is():
    assert describe_condition({"field": "source", "value": "grafana"}) == "Source is grafana"


@pytest.mark.parametrize(
    "cond",
    [
        "source is grafana",
        ["source", "is", "grafana"],
        {"op": "is", "value": "grafana"},
        {"field": 3, "op": "is", "value": "grafana"},
        {"field": "region", "op": "is", "value": "eu"},
        {"field": "label", "op": "is", "value": "eu"},
        {"field": "label:", "op": "is", "value": "eu"},
        {"field": "source:x", "op": "is", "value": "eu"},
        {"field": "source", "op": "matches", "value": "graf.*"},
        {"field": "source", "op": "in", "value": "grafana"},
        {"field": "source", "op": "not-in", "value": ["grafana", 3]},
        {"field": "source", "op": "is", "value": ["grafana"]},
        {"field": "source", "op": "is-not", "value": None},
        {"field": "source", "op": "is"},
    ],
)
def test_describe_condition_inexpressible(cond):
    assert describe_condition(cond) is None


@pytest.mark.parametrize("match", [[], None])
def test_describe_match_empty(match):
    assert describe_match(match) == "Matches everything"


def test_describe_match_joins_with_and():
    match = [
        {"field": "source", "op": "is", "value": "cluster"},
        {"field": "severity", "op": "is-not", "value": "info"},
    ]
    assert describe_match(match) == "Source is cluster and Severity is not Info"


def test_describe_match_mixed_is_unshowable():
    match = [
        {"field": "source", "op": "is", "value": "cluster"},
        {"field": "source", "op": "regex", "value": ".*"},
    ]
    assert describe_match(match) == "Uses conditions the console can't show"


def test_describe_match_not_a_list_is_unshowable():
    assert describe_match({"field": "source"}) == "Uses conditions the console can't show"


def test_representable():
    assert representable([])
    assert representable([{"field": "source", "op": "is", "value": "cluster"}])
    assert not representable([{"field": "source", "op": "regex", "value": ".*"}])
    assert not representable({"field": "source", "op": "is", "value": "cluster"})
    assert not representable(None)
