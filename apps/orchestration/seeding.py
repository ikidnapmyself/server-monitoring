"""Seed the routing table to match how this hub is actually configured.

This is the LIVE version, for the callers that must track today's design:
``setup_cluster`` and the admin. Migration ``0017`` carries a frozen copy of the
lane constants and the create/repair body, and that duplication is deliberate —
if the migration imported this module, replaying it on a fresh database would run
whatever this module had become in the meantime, and a future edit here would
make fresh installs diverge from upgraded ones on the same migration number. Edit
this file freely; the snapshot in ``0017`` stays where it is (a test pins it).

The models are passed in because the callers differ in which classes they hold.

The rule that shapes everything here: **a channel is optional, a lane that lists
``notify`` is not.** ``stages`` is the operator's statement of intent — a hub with
no channel is not broken, it simply has no lane claiming to deliver, and seeding
``notify`` onto it would manufacture an intent it can never satisfy (every run
then failing ``no_channel``). See
docs/plans/2026-08-22-lane-channel-required-design.md §2.1.

And the rule that limits it: **the pipeline definition is hub-side truth.** Only a
configuration-time action changes one, and none of them may silently undo an
operator's decision. Concretely, shaping may only ever turn a lane OFF (a lane
that cannot deliver is safely quiet), never ON, and only a lane still carrying
this seed's own provenance marker is restored later.

Everything here reads ``stages`` as a plain list rather than calling
``routable_stages()``: historical models carry fields only, never methods.
"""

from typing import Any

#: Provenance, written into ``PipelineDefinition.tags``. The seed strips ``notify``
#: from a lane on a channel-less hub; this records that it did, so
#: ``enable_delivery`` can restore exactly those lanes later. Shape cannot answer
#: that question — an operator can type the same ``stages`` list by hand, and that
#: row is theirs, not the seed's to rewrite.
SEED_SHAPE_KEY = "seed_shape"
RECORD_ONLY = "record-only"

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
    "catch-all": ["check", "analyze", "notify"],
}


def _tags(obj) -> dict:
    """``tags`` as a dict. It is a JSONField, so a fixture can persist a list."""
    return dict(obj.tags) if isinstance(obj.tags, dict) else {}


def seed_routing_table(pipeline_model, channel_model) -> dict:
    """Create or repair the default lanes, shaped by how many channels are active.

    Returns ``{"created": n, "repaired": n, "bound": n, "delivering": bool}`` for
    the caller to log.

    **Repair, not just create.** The earlier seeds (0012/0014/0016) run first, so on
    a fresh install every row already exists by the time this executes and
    ``get_or_create``'s defaults would be discarded — the channel-aware shaping would
    never run on the one case it was written for. So a row still carrying exactly the
    shape those migrations seeded is reshaped; a row an operator has touched is not.

    **Repair may only switch a lane off.** A lane that cannot deliver is safely
    quiet, so removing ``notify`` and disabling a lane left with nothing to do is a
    repair. Turning one back on is not: an operator who disabled the ``catch-all``
    must not find it running again because ``migrate`` ran.
    """
    channels = list(channel_model.objects.filter(is_active=True)[:2])
    delivering = bool(channels)

    created = repaired = bound = 0
    for lane in _LANES:
        fields = dict(lane)
        name = fields.pop("name")
        wanted = list(fields.pop("stages"))
        stages = wanted
        record_only = False
        if not delivering and "notify" in wanted:
            # Nothing to deliver to: the lane must not claim it will.
            stages = [s for s in wanted if s != "notify"]
            record_only = True
        # A lane reshaped down to nothing exists only to notify, so it has nothing
        # left to do. A lane the seed never reshaped keeps whatever it lists and
        # stays on: only stripping ``notify`` can empty a lane here.
        is_active = bool(stages) or not record_only
        tags = {SEED_SHAPE_KEY: RECORD_ONLY} if record_only else {}

        obj, was_created = pipeline_model.objects.get_or_create(
            name=name,
            defaults={**fields, "stages": stages, "is_active": is_active, "tags": tags},
        )
        created += int(was_created)

        if not was_created and list(obj.stages or []) == _PRIOR_STAGES.get(name):
            changed = []
            if list(obj.stages or []) != stages:
                obj.stages = stages
                changed.append("stages")
            if record_only and not stages and obj.is_active:
                # Off, never on: see the docstring.
                obj.is_active = False
                changed.append("is_active")
            if record_only:
                marked = _tags(obj)
                marked[SEED_SHAPE_KEY] = RECORD_ONLY
                obj.tags = marked
                changed.append("tags")
            if changed:
                obj.save(update_fields=changed)
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
    """Restore ``notify`` on the lanes THIS seed shaped for a channel-less hub.

    The seed strips ``notify`` when no channel exists, because a lane must not
    promise what the hub cannot do. Configuring a channel is the operator saying
    "deliver" — a CONFIGURATION-time decision, made here, not a runtime one. The
    pipeline never reinterprets a definition; it executes it. So the definition is
    what changes.

    Which lanes those are is read from the ``seed_shape`` marker, not guessed from
    ``stages``: an operator whose ``catch-all`` records by choice writes the very
    same list, and that row is theirs. Restoring a lane spends the claim, so the
    marker is cleared. A lane that still carries the marker but no longer carries
    the shape has been edited since, and is likewise left alone.

    Reactivation is bounded the same way. The seed only ever switched off a lane it
    had reshaped down to *nothing* (``resolved-all-clear``), so only that case is
    switched back on; a lane the operator disabled stays disabled. Returns how many
    lanes were restored plus how many were bound.
    """
    restored = 0
    for lane in _LANES:
        name = lane["name"]
        wanted = list(lane["stages"])
        # No "does this lane notify?" guard: the marker below is the whole gate. The
        # seed writes it only onto a lane it stripped ``notify`` from, so a lane that
        # never promised delivery cannot carry one and is skipped there.
        stripped = [s for s in wanted if s != "notify"]
        obj = pipeline_model.objects.filter(name=name).first()
        if obj is None or _tags(obj).get(SEED_SHAPE_KEY) != RECORD_ONLY:
            continue
        if list(obj.stages or []) != stripped:
            continue
        obj.stages = wanted
        if not stripped:
            obj.is_active = True
        tags = _tags(obj)
        tags.pop(SEED_SHAPE_KEY)
        obj.tags = tags
        obj.save(update_fields=["stages", "is_active", "tags"])
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
