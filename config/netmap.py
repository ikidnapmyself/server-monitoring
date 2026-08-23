"""Network map: a read-time projection of the routing table.

Everything here is computed from ``PipelineDefinition`` + ``NotificationChannel``
at request time — no storage, no caching, no side effects. Shadow detection is
symbolic subsumption over the four match ops; anything unprovable is drawn as a
normal lane (fail toward "not shadowed").

Design: docs/plans/2026-08-23-network-map-design.md
"""


def _as_set(value) -> set | None:
    return set(value) if isinstance(value, (list, tuple)) else None


def _cond_implied(b_conds: list[dict], a_cond: dict) -> bool:
    """True if some condition of lane B provably implies A's condition ``a_cond``.

    B's conditions are AND-ed, so one implying condition on the same field is
    enough. Every unlisted pairing is unprovable → False. The ``op`` default and
    value shapes mirror ``PipelineDefinition.matches()`` exactly.
    """
    field = a_cond.get("field")
    a_op, a_val = a_cond.get("op", "is"), a_cond.get("value")
    for b in b_conds:
        if b.get("field") != field:
            continue
        b_op, b_val = b.get("op", "is"), b.get("value")
        if b_op == "is":
            if a_op == "is" and b_val == a_val:
                return True
            if a_op == "in" and (s := _as_set(a_val)) is not None and b_val in s:
                return True
            if a_op == "is-not" and b_val != a_val:
                return True
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and b_val not in s:
                return True
        elif b_op == "in" and (bs := _as_set(b_val)) is not None:
            if a_op == "is" and bs == {a_val}:
                return True
            if a_op == "in" and (s := _as_set(a_val)) is not None and bs <= s:
                return True
            if a_op == "is-not" and a_val not in bs:
                return True
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and not (bs & s):
                return True
        elif b_op == "is-not":
            if a_op == "is-not" and b_val == a_val:
                return True
            if a_op == "not-in" and _as_set(a_val) == {b_val}:
                return True
        elif b_op == "not-in" and (bs := _as_set(b_val)) is not None:
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and s <= bs:
                return True
            if a_op == "is-not" and a_val in bs:
                return True
    return False


def _lane_shadows(a_match: list, b_match: list) -> bool:
    """True if lane A (earlier, active) provably matches everything lane B matches."""
    return all(_cond_implied(b_match, a_cond) for a_cond in a_match)
