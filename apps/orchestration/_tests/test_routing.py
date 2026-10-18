from datetime import timedelta

import pytest
from django.utils import timezone

from apps.orchestration.models import PipelineDefinition
from apps.orchestration.routing import (
    StaleRoute,
    describe_condition,
    describe_match,
    fallthrough,
    representable,
    save_route,
    split_field,
    value_words,
)
from apps.orchestration.testing import clear_lanes


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
    assert representable(None)
    assert representable({})


@pytest.mark.parametrize(
    "cond",
    [
        {"field": "source", "op": "in", "value": ["a,b"]},
        {"field": "source", "op": "is", "value": " grafana"},
        {"field": "source", "op": "in", "value": ["grafana "]},
        {"field": "label: team", "op": "is", "value": "db"},
        {"field": "label:team ", "op": "is", "value": "db"},
        {"field": "severity", "op": "is", "value": "critcal"},
        {"field": "status", "op": "in", "value": ["firing", "Resolved"]},
        {"field": "origin", "op": "is", "value": "cron"},
        {"field": "source", "op": "is", "value": ""},
        {"field": "source", "op": "in", "value": []},
    ],
)
def test_not_representable_when_rows_cant_reproduce_it(cond):
    assert describe_condition(cond) is not None
    assert not representable([cond])


@pytest.mark.parametrize(
    "cond",
    [
        {"field": "source", "op": "is", "value": "a,b"},
        {"field": "source", "value": "grafana"},
        {"field": "severity", "op": "in", "value": ["critical", "info"]},
        {"field": "label:team", "op": "not-in", "value": ["db", "web"]},
    ],
)
def test_representable_when_rows_reproduce_it(cond):
    assert representable([cond])


@pytest.fixture
def lanes(db):
    clear_lanes()

    def make(name, priority=100, match=None, is_active=True):
        return PipelineDefinition.objects.create(
            name=name,
            priority=priority,
            match=[{"field": "source", "op": "is", "value": name}] if match is None else match,
            is_active=is_active,
        )

    return make


def test_save_route_at_current_version_writes_and_bumps(lanes):
    route = lanes("web")
    save_route(route.pk, {"description": "edited"}, expected=route.version)
    route.refresh_from_db()
    assert route.description == "edited"
    assert route.version == 2


def test_save_route_at_old_version_raises_and_writes_nothing(lanes):
    route = lanes("web")
    PipelineDefinition.objects.filter(pk=route.pk).update(version=2)
    with pytest.raises(StaleRoute):
        save_route(route.pk, {"description": "edited"}, expected=1)
    route.refresh_from_db()
    assert route.description == ""
    assert route.version == 2


def test_save_route_moves_updated_at(lanes):
    route = lanes("web")
    old = timezone.now() - timedelta(days=1)
    PipelineDefinition.objects.filter(pk=route.pk).update(updated_at=old)
    save_route(route.pk, {"priority": 50}, expected=route.version)
    route.refresh_from_db()
    assert route.updated_at > old


def test_fallthrough_inactive_route(lanes):
    route = lanes("web", is_active=False)
    assert fallthrough(route) == "This route is off, so deleting it changes no routing."


def test_fallthrough_names_the_next_active_catch_all(lanes):
    lanes("earlier", priority=10)
    route = lanes("web", priority=100)
    lanes("narrower", priority=200)
    lanes("off-catch-all", priority=300, match=[], is_active=False)
    lanes("catch-all", priority=1000, match=[])
    lanes("later-catch-all", priority=2000, match=[])
    assert fallthrough(route) == (
        "Traffic it matched goes to the next route that matches, at worst catch-all, "
        "which matches everything."
    )


def test_fallthrough_behind_an_earlier_catch_all(lanes):
    lanes("earlier-catch-all", priority=10, match=[])
    route = lanes("web", priority=100)
    lanes("catch-all", priority=1000, match=[])
    assert fallthrough(route) == (
        "Every alert matches earlier-catch-all first, so this route gets no traffic and "
        "deleting it changes no routing."
    )


def test_fallthrough_ignores_an_earlier_inactive_catch_all(lanes):
    lanes("off-catch-all", priority=10, match=[], is_active=False)
    route = lanes("web", priority=100)
    lanes("catch-all", priority=1000, match=[])
    assert "at worst catch-all," in fallthrough(route)


def test_fallthrough_ties_on_priority_break_on_id(lanes):
    route = lanes("web", priority=100)
    after = lanes("after", priority=100, match=[])
    assert route.pk < after.pk
    assert "at worst after," in fallthrough(route)


def test_fallthrough_tie_before_on_id_is_earlier(lanes):
    before = lanes("before", priority=100, match=[])
    route = lanes("web", priority=100)
    assert before.pk < route.pk
    assert fallthrough(route).startswith("Every alert matches before first")


def test_fallthrough_of_a_catch_all_skips_itself(lanes):
    route = lanes("catch-all", priority=1000, match=[])
    assert fallthrough(route) == (
        "Traffic only it matched will match no route, and those runs will fail as unrouted."
    )


def test_fallthrough_without_a_later_catch_all(lanes):
    lanes("earlier", priority=10)
    route = lanes("web", priority=100)
    lanes("off-catch-all", priority=1000, match=[], is_active=False)
    assert fallthrough(route) == (
        "Traffic only it matched will match no route, and those runs will fail as unrouted."
    )
