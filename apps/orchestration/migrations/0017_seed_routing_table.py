"""Seed the full default routing table, shaped by the hub's active channels.

``0012``/``0014``/``0016`` each seeded one lane on the assumption that a lane may
always list ``notify`` — safe only while ``NotifySelector`` quietly picked the
first active channel by name for any lane that named none. That fallback is now
scoped to interactive callers, so a lane listing ``notify`` with no active channel
fails ``no_channel`` instead of delivering somewhere the operator never chose.

Which makes the seed's job different: it must not manufacture an intent the hub
cannot satisfy. **A channel is optional; a lane that lists ``notify`` is not.** So
this reads how many channels are active and seeds to match — zero means the lanes
omit ``notify`` and the hub records rather than failing, one means they list it
and are bound to it, two or more leaves the binding to an operator because
picking by name is the very bug being removed. See
docs/plans/2026-08-22-lane-channel-required-design.md §2.1.

DELIBERATE DUPLICATION of ``apps.orchestration.seeding``
--------------------------------------------------------
The lane constants and the create/repair body below are a frozen copy, and must
stay frozen. This module previously imported the live ``seeding`` module, which
means replaying the migration on a fresh database ran whatever that module had
become in the meantime: edit a lane's stages next year and fresh installs get the
new shape while every upgraded install keeps the old one, from the same migration
number. A migration is a historical snapshot of a schema *and* of the data it
writes, so it carries its own copy and never grows a new one.

``apps.orchestration.seeding`` stays the live version for the callers that must
track today's design — ``setup_cluster`` and the admin — and its tests cover the
behaviour. What is pinned here is only that this snapshot never moves.

``get_or_create`` on ``name`` means this is a no-op wherever the earlier
migrations already seeded these lanes, and an operator's edited row is left
exactly as they have it. The models arrive from ``apps.get_model``, which carries
fields but no methods, so ``stages`` is read as a plain list and
``routable_stages()`` is never called.
"""

from typing import Any

from django.db import migrations

#: Frozen copy of ``apps.orchestration.seeding``'s provenance marker. Records that
#: this seed stripped ``notify`` from a lane, so a later configuration-time action
#: knows which lanes are its to restore.
SEED_SHAPE_KEY = "seed_shape"
RECORD_ONLY = "record-only"

#: Frozen copy of the lanes as designed on 2026-08-22. See the module docstring.
LANES: list[dict[str, Any]] = [
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

#: Frozen copy: what 0012/0014/0016 seeded. A row still carrying exactly this shape
#: has never been edited, so reshaping it is repair, not overwriting a decision.
PRIOR_STAGES = {
    "resolved-all-clear": ["notify"],
    "cluster-nodes": ["analyze", "notify"],
    "hub-self-check": [],
    "catch-all": ["check", "analyze", "notify"],
}


def _tags(obj) -> dict:
    """``tags`` as a dict. It is a JSONField, so a fixture can persist a list."""
    return dict(obj.tags) if isinstance(obj.tags, dict) else {}


def _seed(pipeline_model, channel_model) -> None:
    """Frozen copy of ``seeding.seed_routing_table``. See the module docstring.

    Repair, not merely create: 0012/0014/0016 run first, so every row already
    exists by the time this executes and ``get_or_create``'s defaults would be
    discarded. And repair may only ever switch a lane OFF — a lane that cannot
    deliver is safely quiet, whereas turning one back on would overrule an operator
    who disabled it.
    """
    channels = list(channel_model.objects.filter(is_active=True)[:2])
    delivering = bool(channels)

    for lane in LANES:
        fields = dict(lane)
        name = fields.pop("name")
        wanted = list(fields.pop("stages"))
        stages = wanted
        record_only = False
        if not delivering and "notify" in wanted:
            stages = [s for s in wanted if s != "notify"]
            record_only = True
        is_active = bool(stages) or not record_only
        tags = {SEED_SHAPE_KEY: RECORD_ONLY} if record_only else {}

        obj, was_created = pipeline_model.objects.get_or_create(
            name=name,
            defaults={**fields, "stages": stages, "is_active": is_active, "tags": tags},
        )

        if not was_created and list(obj.stages or []) == PRIOR_STAGES.get(name):
            changed = []
            if list(obj.stages or []) != stages:
                obj.stages = stages
                changed.append("stages")
            if record_only and not stages and obj.is_active:
                obj.is_active = False
                changed.append("is_active")
            if record_only:
                marked = _tags(obj)
                marked[SEED_SHAPE_KEY] = RECORD_ONLY
                obj.tags = marked
                changed.append("tags")
            if changed:
                obj.save(update_fields=changed)

        if (
            len(channels) == 1
            and obj.channel_id is None
            and obj.is_active
            and "notify" in (obj.stages or [])
        ):
            obj.channel = channels[0]
            obj.save(update_fields=["channel"])


def forwards(apps, schema_editor):
    _seed(
        apps.get_model("orchestration", "PipelineDefinition"),
        apps.get_model("notify", "NotificationChannel"),
    )


def backwards(apps, schema_editor):
    """Deliberately nothing.

    Unlike ``0012``/``0016``, which delete a row still matching their exact seeded
    shape, this migration also *binds* a channel — and neither a lane nor an
    operator's channel choice is this migration's to remove. Reversing it leaves
    the routing table alone; deleting lanes on a downgrade would silence the hub.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0016_seed_resolved_lane"),
        # The seed counts active NotificationChannel rows to decide whether a lane
        # may claim to deliver, so that table must exist before this runs.
        ("notify", "0003_remove_notification_log"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
