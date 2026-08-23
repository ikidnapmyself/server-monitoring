"""Tests for the network-map projection (config/netmap.py)."""

import pytest

from config.netmap import _annotate_shadows, _cond_implied, _lane_shadows, _never_matches


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
