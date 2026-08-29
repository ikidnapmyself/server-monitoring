"""Retire the ``hub-self-check`` lane: the hub's own checks route like any node's.

``0014`` seeded a record-only lane (``origin is checker_generated``, empty
``stages``, priority 50) for one reason: the hub's checker cron fires every five
minutes and a still-firing alert was re-reported on every tick, so a lane that
notified would have sent ~288 identical messages a day. The incident change gate
has since closed that — a repeat of an unchanged alert is absorbed and enqueues
nothing — so the reason the lane existed is gone.

What is left is a hazard. Checker alerts now carry ``source: cluster``, because
Alert identity is the pair ``(fingerprint, source)`` and the hub's own alerts must
share identity with the same machine's pushed ones. A hub-local run therefore
matches BOTH this lane and ``cluster-nodes`` — same priority 50 — and
``resolve_pipeline`` settles the tie on ``id``, i.e. on which row happened to be
seeded first. Retiring this lane leaves one answer: the hub's own checks take the
node lane, analyse and notify, and the hub can page about its own full disk
exactly as it pages about any other node's.

DEACTIVATED, NEVER DELETED. ``Incident.pipeline`` is a FK with
``on_delete=SET_NULL``, so deleting the row would blank the record of which lane
handled every incident it ever routed. ``resolve_pipeline`` filters on
``is_active=True``, so flipping the flag stops the routing immediately and keeps
the history readable.

Only a row still carrying exactly the shape ``0014``/``0017`` seeded is touched —
the same principle stated in ``apps.orchestration.seeding``: an untouched row is
this project's to repair, an edited one is the operator's. A row that differs is
kept and logged by name so an operator can find it and decide.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

NAME = "hub-self-check"

#: Provenance, written into ``PipelineDefinition.tags`` — the same mechanism
#: ``apps.orchestration.seeding`` uses for ``SEED_SHAPE_KEY``, and for the same
#: reason: shape cannot say WHO turned a lane off. An operator may have disabled
#: this lane themselves before the migration ran, and ``backwards`` must not
#: overrule that decision. Only a row carrying this marker was switched off by
#: ``forwards``, so only that row is switched back on (and the marker cleared).
RETIRED_BY_KEY = "retired_by"
RETIRED_BY = "0018_retire_hub_self_check"

#: The shape ``0014``/``0017`` seeded. Anything else is an operator's row.
SEEDED_SHAPE = {
    "match": [{"field": "origin", "op": "is", "value": "checker_generated"}],
    "stages": [],
    "priority": 50,
}


def _tags(row) -> dict:
    """``tags`` as a dict. It is a JSONField, so a fixture can persist a list."""
    return dict(row.tags) if isinstance(row.tags, dict) else {}


def forwards(apps, schema_editor):
    pipeline_model = apps.get_model("orchestration", "PipelineDefinition")
    if not pipeline_model.objects.filter(name=NAME).exists():
        return
    if not pipeline_model.objects.filter(name=NAME, is_active=True).exists():
        # Already off — by an operator, or by an earlier run of this migration.
        # Either way there is nothing to retire, and no marker is written: this
        # run did not switch it off, so it has no claim to switch it back on.
        logger.info(
            "Left pipeline definition %r alone: it is already inactive.",
            NAME,
        )
        return
    row = pipeline_model.objects.filter(name=NAME, is_active=True, **SEEDED_SHAPE).first()
    if row is None:
        logger.info(
            "Kept pipeline definition %r: it no longer carries the seeded shape, "
            "so retiring it would overwrite an operator's decision.",
            NAME,
        )
        return
    row.is_active = False
    row.tags = {**_tags(row), RETIRED_BY_KEY: RETIRED_BY}
    row.save(update_fields=["is_active", "tags"])


def backwards(apps, schema_editor):
    """Reactivate the row only if THIS migration is the one that switched it off.

    The marker is the whole test. Matching on shape alone could not tell a row
    ``forwards`` deactivated from one an operator disabled themselves, and a
    rollback that silently re-enabled a lane the operator had turned off would
    overrule them.
    """
    pipeline_model = apps.get_model("orchestration", "PipelineDefinition")
    row = pipeline_model.objects.filter(name=NAME, is_active=False).first()
    if row is None:
        return
    tags = _tags(row)
    if tags.pop(RETIRED_BY_KEY, None) != RETIRED_BY:
        logger.info(
            "Left pipeline definition %r deactivated: this migration did not "
            "switch it off, so re-enabling it would overrule an operator.",
            NAME,
        )
        return
    row.is_active = True
    row.tags = tags
    row.save(update_fields=["is_active", "tags"])


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0017_seed_routing_table"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
