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


#: What migrations 0012/0014/0016 seeded, before this module existed. A row still
#: carrying exactly this shape has never been edited, so reshaping it is repair
#: rather than overwriting an operator's decision. Anything else is left alone.
_PRIOR_STAGES = {
    "resolved-all-clear": ["notify"],
    "cluster-nodes": ["analyze", "notify"],
    "hub-self-check": [],
    "catch-all": ["check", "analyze", "notify"],
}


def seed_routing_table(pipeline_model, channel_model) -> dict:
    """Create or repair the default lanes, shaped by how many channels are active.

    Returns ``{"created": n, "repaired": n, "bound": n, "delivering": bool}`` for
    the caller to log.

    **Repair, not just create.** The earlier seeds (0012/0014/0016) run first, so on
    a fresh install every row already exists by the time this executes and
    ``get_or_create``'s defaults would be discarded — the channel-aware shaping would
    never run on the one case it was written for. So a row still carrying exactly the
    shape those migrations seeded is reshaped; a row an operator has touched is not.
    """
    channels = list(channel_model.objects.filter(is_active=True)[:2])
    delivering = bool(channels)

    created = repaired = bound = 0
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

        if not was_created and list(obj.stages or []) == _PRIOR_STAGES.get(name):
            if list(obj.stages or []) != stages or obj.is_active != is_active:
                obj.stages = stages
                obj.is_active = is_active
                obj.save(update_fields=["stages", "is_active"])
                repaired += 1

        # Bind only when the answer is not arbitrary, and only into an empty slot on
        # a lane that can actually run. An inactive lane routes nothing, so giving it
        # a channel is at best noise and at worst touching a row an operator
        # deliberately switched off.
        if (
            len(channels) == 1
            and obj.channel_id is None
            and obj.is_active
            and "notify" in (obj.stages or [])
        ):
            obj.channel = channels[0]
            obj.save(update_fields=["channel"])
            bound += 1

    return {
        "created": created,
        "repaired": repaired,
        "bound": bound,
        "delivering": delivering,
    }


def enable_delivery(pipeline_model, channel) -> int:
    """Restore ``notify`` on seeded lanes that were shaped for a channel-less hub.

    The seed strips ``notify`` when no channel exists, because a lane must not
    promise what the hub cannot do. Configuring a channel is the operator saying
    "deliver" — a CONFIGURATION-time decision, made here, not a runtime one. The
    pipeline never reinterprets a definition; it executes it. So the definition is
    what changes.

    Only lanes still carrying the record-only shape this module wrote are touched;
    a lane an operator has edited is theirs. Returns how many were restored.
    """
    restored = 0
    for lane in _LANES:
        name = lane["name"]
        wanted = list(lane["stages"])
        if "notify" not in wanted:
            continue
        stripped = [s for s in wanted if s != "notify"]
        obj = pipeline_model.objects.filter(name=name).first()
        if obj is None or list(obj.stages or []) != stripped:
            continue
        obj.stages = wanted
        obj.is_active = True
        obj.save(update_fields=["stages", "is_active"])
        restored += 1
    return restored + bind_delivering_lanes(pipeline_model, channel)


def bind_delivering_lanes(pipeline_model, channel) -> int:
    """Point every unbound lane that lists ``notify`` at ``channel``. Return how many.

    For the moment a channel comes into existence — ``setup_cluster`` — rather than
    migrate time, when there is usually no channel yet. It binds EVERY delivering
    lane, not just the catch-all: ``cluster-nodes`` and ``resolved-all-clear`` carry
    node pushes and all-clears, the hub's primary traffic, and a lane that lists
    ``notify`` with no channel now fails ``no_channel`` rather than falling through
    to "first active channel by name".

    Only fills a NULL slot on an active lane: a lane an operator has already pointed
    somewhere, or deliberately switched off, is left alone.
    """
    bound = 0
    for lane in pipeline_model.objects.filter(channel__isnull=True, is_active=True):
        if "notify" not in (lane.stages or []):
            continue
        lane.channel = channel
        lane.save(update_fields=["channel"])
        bound += 1
    return bound
