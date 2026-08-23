"""Tests for the network-map projection (config/netmap.py)."""

import pytest

from config.netmap import _cond_implied, _lane_shadows


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
