"""Tests for the network-map projection (config/netmap.py)."""

import pytest

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes
from config.describing import Action
from config.netmap import (
    _annotate_shadows,
    _cond_implied,
    _lane_shadows,
    _never_matches,
    get_map_context,
)


def cond(field, op, value):
    return {"field": field, "op": op, "value": value}


class TestCondImplied:
    """_cond_implied(b_conds, a_cond): do B's conditions on a field prove A's?"""

    @pytest.mark.parametrize(
        "b,a,expected",
        [
            # is / is
            ([cond("source", "is", "cluster")], cond("source", "is", "cluster"), True),
            ([cond("source", "is", "cluster")], cond("source", "is", "grafana"), False),
            # is ⇒ in / is-not / not-in
            ([cond("sev", "is", "high")], cond("sev", "in", ["high", "critical"]), True),
            ([cond("sev", "is", "high")], cond("sev", "in", ["low"]), False),
            ([cond("sev", "is", "high")], cond("sev", "is-not", "low"), True),
            ([cond("sev", "is", "high")], cond("sev", "is-not", "high"), False),
            ([cond("sev", "is", "high")], cond("sev", "not-in", ["low", "info"]), True),
            ([cond("sev", "is", "high")], cond("sev", "not-in", ["high"]), False),
            # in ⇒ ...
            ([cond("sev", "in", ["high"])], cond("sev", "is", "high"), True),
            ([cond("sev", "in", ["high", "critical"])], cond("sev", "is", "high"), False),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "in", ["a", "b", "c"]), True),
            ([cond("sev", "in", ["a", "d"])], cond("sev", "in", ["a", "b", "c"]), False),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "is-not", "c"), True),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "is-not", "a"), False),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "not-in", ["c", "d"]), True),
            ([cond("sev", "in", ["a", "b"])], cond("sev", "not-in", ["b"]), False),
            # negation ⇒ negation
            ([cond("sev", "is-not", "low")], cond("sev", "is-not", "low"), True),
            ([cond("sev", "is-not", "low")], cond("sev", "is-not", "high"), False),
            ([cond("sev", "not-in", ["a", "b"])], cond("sev", "not-in", ["a"]), True),
            ([cond("sev", "not-in", ["a"])], cond("sev", "not-in", ["a", "b"]), False),
            ([cond("sev", "is-not", "a")], cond("sev", "not-in", ["a"]), True),
            ([cond("sev", "is-not", "a")], cond("sev", "not-in", ["a", "b"]), False),
            ([cond("sev", "not-in", ["a", "b"])], cond("sev", "is-not", "a"), True),
            ([cond("sev", "not-in", ["b"])], cond("sev", "is-not", "a"), False),
            # open-domain: negation on B cannot prove a positive on A
            ([cond("sev", "is-not", "low")], cond("sev", "is", "high"), False),
            ([cond("sev", "not-in", ["low"])], cond("sev", "in", ["high"]), False),
            # B has no condition on the field → unprovable
            ([], cond("sev", "is", "high"), False),
            ([cond("other", "is", "x")], cond("sev", "is", "high"), False),
            # any B condition on the field proving A suffices
            (
                [cond("sev", "is-not", "x"), cond("sev", "is", "high")],
                cond("sev", "in", ["high"]),
                True,
            ),
            # default op is "is" (mirrors matches())
            ([{"field": "sev", "value": "high"}], cond("sev", "is", "high"), True),
            # unhashable JSON values are unprovable, never a crash
            ([cond("sev", "in", [["a"], "b"])], cond("sev", "is", "b"), False),
            ([cond("sev", "is", "b")], cond("sev", "in", [["a"], "b"]), False),
            ([cond("sev", "is", ["b"])], cond("sev", "in", ["b", "c"]), False),
            ([cond("sev", "is", ["b"])], cond("sev", "not-in", ["z"]), False),
            ([cond("sev", "in", ["x"])], cond("sev", "is", ["x"]), False),
            ([cond("sev", "in", ["x"])], cond("sev", "is-not", ["y"]), False),
            ([cond("sev", "is-not", ["y"])], cond("sev", "not-in", [["y"]]), False),
            ([cond("sev", "not-in", ["x"])], cond("sev", "is-not", ["x"]), False),
            # membership op with a non-list value on the B side is unprovable
            ([cond("sev", "in", "xy")], cond("sev", "is", "x"), False),
        ],
    )
    def test_implication_table(self, b, a, expected):
        assert _cond_implied(b, a) is expected


class TestLaneShadows:
    def test_empty_match_shadows_everything(self):
        assert _lane_shadows([], [cond("sev", "is", "high")]) is True

    def test_nonempty_cannot_shadow_catch_all(self):
        assert _lane_shadows([cond("sev", "is", "high")], []) is False

    def test_every_a_cond_must_be_proven(self):
        a = [cond("source", "is", "cluster"), cond("sev", "is", "high")]
        b = [cond("source", "is", "cluster")]
        assert _lane_shadows(a, b) is False

    def test_superset_by_conditions(self):
        a = [cond("source", "is", "cluster")]
        b = [cond("source", "is", "cluster"), cond("sev", "is", "high")]
        assert _lane_shadows(a, b) is True


class TestNeverMatches:
    """Static mirror of matches() fail-closed rules."""

    @pytest.mark.parametrize(
        "match,expected",
        [
            ([], False),
            ([cond("sev", "is", "high")], False),
            (["not-a-dict"], True),
            ([cond("sev", "frobnicate", "x")], True),
            ([cond("sev", "in", "high")], True),  # membership needs a list
            ([cond("sev", "not-in", "high")], True),
            (None, False),  # falsy non-list → catch-all in matches()
            ({}, False),  # falsy dict → catch-all
            ("junk", True),  # truthy non-list iterates junk in matches() → fails closed
            ({"a": 1}, True),  # truthy non-list
        ],
    )
    def test_table(self, match, expected):
        assert _never_matches(match) is expected


def lane(name, match, active=True):
    """Stand-in with the attributes _annotate_shadows reads."""
    return type("L", (), {"name": name, "match": match, "is_active": active})()


class TestAnnotateShadows:
    def test_earlier_catch_all_shadows_later(self):
        lanes = [lane("catch-all", []), lane("cluster", [cond("source", "is", "cluster")])]
        assert _annotate_shadows(lanes) == {"cluster": "catch-all"}

    def test_first_shadower_named(self):
        lanes = [
            lane("a", [cond("s", "in", ["x", "y"])]),
            lane("b", [cond("s", "in", ["x", "y", "z"])]),
            lane("c", [cond("s", "is", "x")]),
        ]
        # c is shadowed by a (first prover wins); b is not shadowed by a
        assert _annotate_shadows(lanes) == {"c": "a"}

    def test_inactive_lane_does_not_shadow(self):
        lanes = [lane("off", [], active=False), lane("cluster", [cond("s", "is", "x")])]
        assert _annotate_shadows(lanes) == {}

    def test_inactive_and_never_match_lanes_not_marked_shadowed(self):
        lanes = [
            lane("catch-all", []),
            lane("off", [cond("s", "is", "x")], active=False),
            lane("broken", [cond("s", "bad-op", "x")]),
        ]
        assert _annotate_shadows(lanes) == {}

    def test_never_matching_lane_does_not_shadow(self):
        lanes = [lane("broken", [cond("s", "bad-op", "x")]), lane("b", [cond("s", "is", "x")])]
        assert _annotate_shadows(lanes) == {}

    def test_truthy_non_list_match_neither_shadows_nor_crashes(self):
        lanes = [lane("junky", "junk"), lane("b", [cond("s", "is", "x")])]
        assert _annotate_shadows(lanes) == {}

    def test_unhashable_match_values_neither_shadow_nor_crash(self):
        lanes = [
            lane("weird", [cond("s", "in", [["a"], "b"])]),
            lane("plain", [cond("s", "is", "b")]),
        ]
        assert _annotate_shadows(lanes) == {}

    def test_shadowed_lane_still_joins_candidates(self):
        # "mid" is marked shadowed yet stays a candidate for later lanes;
        # attribution names the first prover in lane order ("wide").
        lanes = [
            lane("wide", [cond("s", "in", ["x", "y"])]),
            lane("mid", [cond("s", "in", ["x", "y"])]),
            lane("narrow", [cond("s", "in", ["x", "y"]), cond("t", "is", "1")]),
        ]
        assert _annotate_shadows(lanes) == {"mid": "wide", "narrow": "wide"}

    def test_chained_attribution_when_provability_is_not_transitive(self):
        # Provability is not transitive for degenerate empty-list values: A proves
        # B (b's ``in ["x"]`` gives ``bs == {"x"}``), and B proves C (``∅ ⊆ {"x"}``),
        # but A does not prove C directly (``∅ == {"x"}`` fails). C is still
        # semantically dead — its shadower is itself shadowed — so attribution may
        # name an itself-shadowed lane, and that chain is truthful, not a bug.
        lanes = [
            lane("A-name", [cond("s", "is", "x")]),
            lane("B-name", [cond("s", "in", ["x"])]),
            lane("C-name", [cond("s", "in", [])]),
        ]
        assert _annotate_shadows(lanes) == {"B-name": "A-name", "C-name": "B-name"}


@pytest.mark.django_db
class TestGetMapContext:
    @pytest.fixture(autouse=True)
    def _empty_table(self):
        # Migration 0012 seeds lanes into every test DB; these tests assert on
        # exact lane lists, so they must start from an empty routing table.
        clear_lanes()

    def _lane(self, name, priority, **kw):
        return PipelineDefinition.objects.create(name=name, priority=priority, **kw)

    def test_lanes_in_match_order(self):
        # Mirrors resolve_pipeline's order_by("priority", "id"), minus is_active.
        self._lane("late", priority=200, match=[])
        self._lane("early", priority=10, match=[])
        self._lane("tie-second", priority=10, match=[])
        names = [c["name"] for c in get_map_context()["lanes"]]
        assert names == ["early", "tie-second", "late"]

    def test_card_fields_bound_lane(self):
        ch = NotificationChannel.objects.create(
            name="ops", driver="email", is_active=True, config={}
        )
        lane_row = self._lane(
            "cluster",
            priority=10,
            match=[{"field": "source", "op": "is", "value": "cluster"}],
            stages=["check", "analyze", "notify"],
            channel=ch,
            # Deliberate literal, not SEED_SHAPE_KEY: pins the wire format of the tag.
            tags={"seed_shape": "record-only"},
        )
        card = get_map_context()["lanes"][0]
        assert card["pk"] == lane_row.pk
        assert "admin_url" not in card
        assert card["state"] == "ok"
        assert card["state_words"] == ""
        assert card["conditions"] == ["Source is cluster"]
        assert card["stages"] == ["Diagnose", "Analyze", "Notify"]
        assert card["delivery"]["state"] == "bound"
        assert card["delivery"]["channel"] == "ops"
        assert card["delivery"]["words"] == "Delivers to ops"
        assert card["seed_shaped"] is True
        assert card["seed_note"] == (
            "Set up without delivery. It delivers once a channel is active."
        )
        assert card["edit"] == Action("Edit route", "edit_route", {"pk": lane_row.pk})

    def test_a_lane_without_the_seed_tag_carries_no_note(self):
        self._lane("plain", priority=1, match=[], stages=["check"])
        card = get_map_context()["lanes"][0]
        assert card["seed_shaped"] is False
        assert card["seed_note"] == ""

    def test_a_lane_with_no_stages_records_only(self):
        self._lane("bare", priority=1, match=[], stages=[])
        assert get_map_context()["lanes"][0]["stages"] == ["Records only"]

    def test_no_channel_carries_inactive_channel_name(self):
        # A lane bound to an INACTIVE channel is a no_channel gap, but the card
        # still names the bound channel.
        ch = NotificationChannel.objects.create(
            name="dormant", driver="email", is_active=False, config={}
        )
        self._lane(
            "sleepy",
            priority=1,
            match=[{"field": "source", "op": "is", "value": "z"}],
            stages=["notify"],
            channel=ch,
        )
        card = get_map_context()["lanes"][0]
        assert card["delivery"]["state"] == "no_channel"
        assert card["delivery"]["channel"] == "dormant"

    def test_delivery_states(self):
        self._lane("rec", priority=1, match=[cond("source", "is", "a")], stages=["check"])
        self._lane("gap", priority=2, match=[cond("source", "is", "b")], stages=["notify"])
        ch = NotificationChannel.objects.create(
            name="ghost", driver="not-a-driver", is_active=True, config={}
        )
        self._lane(
            "bad",
            priority=3,
            match=[cond("source", "is", "c")],
            stages=["notify"],
            channel=ch,
        )
        by_name = {c["name"]: c for c in get_map_context()["lanes"]}

        def state_channel_words(card):
            d = card["delivery"]
            return (d["state"], d["channel"], d["words"])

        assert state_channel_words(by_name["rec"]) == (
            "recording-only",
            None,
            "Records only, sends nothing",
        )
        assert state_channel_words(by_name["gap"]) == (
            "no_channel",
            None,
            "Can't deliver: the matched route has no active channel",
        )
        assert state_channel_words(by_name["bad"]) == (
            "no_driver",
            "ghost",
            "Can't deliver: the channel uses a driver this hub doesn't have",
        )

    def test_states_precedence_and_their_words(self):
        self._lane("all", priority=1, match=[])
        self._lane("shadowed", priority=2, match=[cond("source", "is", "x")])
        self._lane("off", priority=3, match=[], is_active=False)
        self._lane("broken", priority=4, match=[cond("source", "zap", 1)])
        by_name = {c["name"]: c for c in get_map_context()["lanes"]}
        assert by_name["all"]["conditions"] == []
        assert by_name["all"]["catch_all"] is True
        assert (by_name["all"]["state"], by_name["all"]["state_words"]) == ("ok", "")
        assert by_name["shadowed"]["state"] == "shadowed"
        assert by_name["shadowed"]["shadowed_by"] == "all"
        assert by_name["shadowed"]["state_words"] == (
            "Never reached: all catches its traffic first"
        )
        assert (by_name["off"]["state"], by_name["off"]["state_words"]) == ("inactive", "Off")
        assert (by_name["broken"]["state"], by_name["broken"]["state_words"]) == (
            "never-matches",
            "Never matches",
        )
        assert by_name["broken"]["conditions"] == ["A condition the console can't show"]

    def test_junk_columns_render_without_crashing(self):
        # Non-list match, non-dict conds, non-dict tags: objects.create() bypasses
        # clean(), so readers must not trust the columns.
        self._lane("junk-match", priority=1, match={"field": "s"}, tags=["not-a-dict"])
        self._lane("junk-cond", priority=2, match=["not-a-dict"])
        by_name = {c["name"]: c for c in get_map_context()["lanes"]}
        assert by_name["junk-match"]["state"] == "never-matches"
        assert by_name["junk-match"]["catch_all"] is False
        assert by_name["junk-match"]["conditions"] == ["A condition the console can't show"]
        assert by_name["junk-match"]["seed_shaped"] is False
        assert by_name["junk-cond"]["state"] == "never-matches"
        assert by_name["junk-cond"]["conditions"] == ["A condition the console can't show"]

    def test_a_falsy_non_list_match_is_a_catch_all(self):
        self._lane("empty-dict", priority=1, match={})
        card = get_map_context()["lanes"][0]
        assert card["catch_all"] is True
        assert card["conditions"] == []

    def test_list_valued_condition_reads_as_words(self):
        self._lane(
            "listy",
            priority=1,
            match=[{"field": "severity", "op": "in", "value": ["critical", "warning"]}],
        )
        card = get_map_context()["lanes"][0]
        assert card["conditions"] == ["Severity is one of Critical, Warning"]

    def test_empty_table(self):
        assert get_map_context()["lanes"] == []


@pytest.mark.django_db
class TestDeliveryAction:
    """A card that reports delivery says where it's changed, as an intent."""

    def setup_method(self):
        clear_lanes()

    def _lane(self, name, **kw):
        return PipelineDefinition.objects.create(name=name, priority=1, **kw)

    def _channel(self, name, **kw):
        return NotificationChannel.objects.create(name=name, config={}, **kw)

    def test_a_bound_channel_opens_the_channel(self):
        ch = self._channel("ops", driver="email", is_active=True)
        self._lane("l", match=[cond("source", "is", "a")], stages=["notify"], channel=ch)
        action = get_map_context()["lanes"][0]["delivery"]["action"]
        assert action == Action("Open ops", "edit_channel", {"pk": ch.pk, "name": "ops"})

    def test_an_inactive_channel_opens_the_channel(self):
        ch = self._channel("dormant", driver="email", is_active=False)
        self._lane("l", match=[cond("source", "is", "a")], stages=["notify"], channel=ch)
        action = get_map_context()["lanes"][0]["delivery"]["action"]
        assert action == Action("Open dormant", "edit_channel", {"pk": ch.pk, "name": "dormant"})

    def test_an_unregistered_driver_opens_the_channel(self):
        ch = self._channel("ghost", driver="not-a-driver", is_active=True)
        self._lane("l", match=[cond("source", "is", "a")], stages=["notify"], channel=ch)
        card = get_map_context()["lanes"][0]
        assert card["delivery"]["state"] == "no_driver"
        assert card["delivery"]["action"].intent == "edit_channel"

    def test_a_lane_with_no_channel_opens_the_route(self):
        lane_row = self._lane("l", match=[cond("source", "is", "a")], stages=["notify"])
        action = get_map_context()["lanes"][0]["delivery"]["action"]
        assert action == Action("Give it an active channel", "edit_route", {"pk": lane_row.pk})

    def test_a_recording_only_lane_has_nothing_to_fix(self):
        self._lane("l", match=[cond("source", "is", "a")], stages=["check"])
        card = get_map_context()["lanes"][0]
        assert card["delivery"]["state"] == "recording-only"
        assert card["delivery"]["action"] is None


def test_the_map_builds_no_urls():
    import config.netmap as netmap

    source = open(netmap.__file__).read()
    assert "reverse" not in source
    assert "admin_links" not in source
    assert "apps.console" not in source
