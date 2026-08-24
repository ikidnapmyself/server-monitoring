"""Network map: a read-time projection of the routing table.

Everything here is computed from ``PipelineDefinition`` + ``NotificationChannel``
at request time — no storage, no caching, no side effects. Shadow detection is
symbolic subsumption over the four match ops; anything unprovable is drawn as a
normal lane (fail toward "not shadowed").

Design: docs/plans/2026-08-23-network-map-design.md
"""

from django.urls import reverse

from apps.orchestration.models import PipelineDefinition
from apps.orchestration.seeding import SEED_SHAPE_KEY


def _as_set(value) -> set | None:
    if not isinstance(value, (list, tuple)):
        return None
    try:
        return set(value)
    except TypeError:  # unhashable members (nested lists/dicts) — unprovable
        return None


def _provable(step) -> bool:
    """Run one proof step; an unhashable value (TypeError) is simply unprovable."""
    try:
        return step()
    except TypeError:
        return False


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
            if a_op == "in" and (s := _as_set(a_val)) is not None and _provable(lambda: b_val in s):
                return True
            if a_op == "is-not" and b_val != a_val:
                return True
            if (
                a_op == "not-in"
                and (s := _as_set(a_val)) is not None
                and _provable(lambda: b_val not in s)
            ):
                return True
        elif b_op == "in" and (bs := _as_set(b_val)) is not None:
            if a_op == "is" and _provable(lambda: bs == {a_val}):
                return True
            if a_op == "in" and (s := _as_set(a_val)) is not None and bs <= s:
                return True
            if a_op == "is-not" and _provable(lambda: a_val not in bs):
                return True
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and not (bs & s):
                return True
        elif b_op == "is-not":
            if a_op == "is-not" and b_val == a_val:
                return True
            if a_op == "not-in" and _provable(lambda: _as_set(a_val) == {b_val}):
                return True
        elif b_op == "not-in" and (bs := _as_set(b_val)) is not None:
            if a_op == "not-in" and (s := _as_set(a_val)) is not None and s <= bs:
                return True
            if a_op == "is-not" and _provable(lambda: a_val in bs):
                return True
    return False


def _lane_shadows(a_match: list, b_match: list) -> bool:
    """True if lane A (earlier, active) provably matches everything lane B matches."""
    return all(_cond_implied(b_match, a_cond) for a_cond in a_match)


_MATCH_OPS = ("is", "is-not", "in", "not-in")


def _never_matches(match) -> bool:
    """Static mirror of ``PipelineDefinition.matches()`` fail-closed rules.

    A condition that can never hold — non-dict, unknown op, membership without a
    list — makes the whole lane unmatchable. A falsy non-list ``match`` (None,
    ``{}``) iterates as empty in ``matches()`` (catch-all), so it is NOT
    never-matching; a truthy non-list iterates junk there and fails closed, so
    it IS. Emptiness/contradiction (e.g. ``in []``) is deliberately not
    checked — conservative toward drawing a normal lane.
    """
    if not isinstance(match, list):
        return bool(match)  # falsy → catch-all in matches(); truthy → iterates junk, fails closed
    for c in match:
        if not isinstance(c, dict):
            return True
        op = c.get("op", "is")
        if op not in _MATCH_OPS:
            return True
        if op in ("in", "not-in") and not isinstance(c.get("value"), (list, tuple)):
            return True
    return False


def _annotate_shadows(lanes) -> dict[str, str]:
    """Map lane name → name of the earlier active lane that provably shadows it.

    ``lanes`` must already be in match (priority) order. Inactive and
    never-matching lanes neither shadow nor get marked shadowed — they have
    their own states on the map.

    Cost is O(n²·c²) over lanes and their conditions — fine for a read-time
    projection over a routing table of dozens of rows. Attribution names the
    first prover in lane order, which may itself be a shadowed lane
    (truthful, but chained — the shadower's shadower took the traffic).
    """
    shadowed: dict[str, str] = {}
    candidates: list = []  # earlier lanes that can actually win traffic
    for b in lanes:
        if b.is_active and not _never_matches(b.match):
            for a in candidates:
                if _lane_shadows(a.match or [], b.match or []):
                    shadowed[b.name] = a.name
                    break
            candidates.append(b)
    return shadowed


def _render_condition(c: dict) -> str:
    op, value = c.get("op", "is"), c.get("value")
    if isinstance(value, (list, tuple)):
        value = "[" + ", ".join(str(v) for v in value) + "]"
    return f"{c.get('field')} {op} {value}"


def _delivery(lane) -> dict:
    """Delivery state for one lane, asking the model helpers — never re-derived."""
    if "notify" not in lane.routable_stages():
        return {"state": "recording-only", "channel": None}
    gap = lane.delivery_gap()
    if gap is None:
        return {"state": "bound", "channel": lane.routed_channel().name}
    return {"state": gap, "channel": lane.channel.name if lane.channel else None}


def get_map_context() -> dict:
    """Template context for the network map: one card per lane, in match order.

    The ordering copies ``resolve_pipeline`` exactly — ``order_by("priority",
    "id")`` — minus its ``is_active`` filter: the map shows every lane, active
    or not.
    """
    lanes = list(PipelineDefinition.objects.select_related("channel").order_by("priority", "id"))
    shadows = _annotate_shadows(lanes)
    cards = []
    for lane in lanes:
        conds = lane.match if isinstance(lane.match, list) else []
        if not lane.is_active:
            state = "inactive"
        elif _never_matches(lane.match):
            state = "never-matches"
        elif lane.name in shadows:
            state = "shadowed"
        else:
            state = "ok"
        cards.append(
            {
                "name": lane.name,
                "state": state,
                "shadowed_by": shadows.get(lane.name),
                "catch_all": not conds,
                "conditions": [
                    _render_condition(c) if isinstance(c, dict) else repr(c) for c in conds
                ],
                "stages": lane.routable_stages(),
                "delivery": _delivery(lane),
                # SEED_SHAPE_KEY means "the seed stripped `notify` from this lane on a
                # channel-less hub; delivery auto-restores once a channel is configured"
                # — NOT "row created by the seed". The badge must say that.
                "seed_shaped": (
                    bool(lane.tags.get(SEED_SHAPE_KEY)) if isinstance(lane.tags, dict) else False
                ),
                "admin_url": reverse(
                    "admin:orchestration_pipelinedefinition_change", args=[lane.pk]
                ),
            }
        )
    return {"lanes": cards}
