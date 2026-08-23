"""Seed the routing table to match how this hub is actually configured.

Shared by the data migration and its tests: migrations must use
``apps.get_model``, tests want the real classes, so the models are passed in.

The rule that shapes everything here: **a channel is optional, a lane that lists
``notify`` is not.** ``stages`` is the operator's statement of intent — a hub with
no channel is not broken, it simply has no lane claiming to deliver, and seeding
``notify`` onto it would manufacture an intent it can never satisfy (every run
then failing ``no_channel``). See
docs/plans/2026-08-22-lane-channel-required-design.md §2.1.

Everything here reads ``stages`` as a plain list rather than calling
``routable_stages()``: a migration's historical models carry fields only, never
methods, and this module runs under both.
"""

from typing import Any

# Annotated so ``stages`` reads back as a list rather than ``object``: the values
# here are heterogeneous (str, list, int) and an inferred ``dict[str, object]``
# makes ``list(fields.pop("stages"))`` untypeable.
_LANES: list[dict[str, Any]] = [
    {
        "name": "resolved-all-clear",
        "description": (
            "Resolved incidents notify without analysis: there is nothing left to "
            "diagnose, and an LLM call on an all-clear is pure cost."
        ),
        "match": [{"field": "status", "op": "is", "value": "resolved"}],
        "stages": ["notify"],
        "priority": 40,
    },
    {
        "name": "cluster-nodes",
        "description": (
            "Alerts pushed by a node. CHECK is omitted: the node already ran its own "
            "checkers, so hub-side checks would report the hub's CPU and disk."
        ),
        "match": [{"field": "source", "op": "is", "value": "cluster"}],
        "stages": ["analyze", "notify"],
        "priority": 50,
    },
    {
        "name": "hub-self-check",
        "description": (
            "The hub's own scheduled checks. Records and correlates only: the cron "
            "repeats every five minutes and a still-firing alert is re-reported each "
            "time."
        ),
        "match": [{"field": "origin", "op": "is", "value": "checker_generated"}],
        "stages": [],
        "priority": 50,
    },
    {
        "name": "catch-all",
        "description": "Everything else. The routing table's last word, as an editable row.",
        "match": [],
        "stages": ["check", "analyze", "notify"],
        "priority": 1000,
    },
]


def seed_routing_table(pipeline_model, channel_model) -> dict:
    """Create the default lanes, shaped by how many channels are active.

    Returns ``{"created": n, "bound": n, "delivering": bool}`` for the caller to
    log. Idempotent: ``get_or_create`` on ``name`` never rewrites an operator's row.
    """
    channels = list(channel_model.objects.filter(is_active=True)[:2])
    delivering = bool(channels)

    created = bound = 0
    for lane in _LANES:
        fields = dict(lane)
        name = fields.pop("name")
        stages = list(fields.pop("stages"))
        if not delivering:
            # Nothing to deliver to: the lane must not claim it will.
            stages = [s for s in stages if s != "notify"]
        # A lane that exists only to notify has nothing left to do.
        is_active = bool(stages) or name == "hub-self-check"

        obj, was_created = pipeline_model.objects.get_or_create(
            name=name, defaults={**fields, "stages": stages, "is_active": is_active}
        )
        created += int(was_created)

        # Bind only when the answer is not arbitrary, and only into an empty slot.
        if len(channels) == 1 and obj.channel_id is None and "notify" in (obj.stages or []):
            obj.channel = channels[0]
            obj.save(update_fields=["channel"])
            bound += 1

    return {"created": created, "bound": bound, "delivering": delivering}
