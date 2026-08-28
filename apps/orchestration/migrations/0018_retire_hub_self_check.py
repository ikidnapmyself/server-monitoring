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

#: The shape ``0014``/``0017`` seeded. Anything else is an operator's row.
SEEDED_SHAPE = {
    "match": [{"field": "origin", "op": "is", "value": "checker_generated"}],
    "stages": [],
    "priority": 50,
}


def _seeded_row(pipeline_model, is_active):
    return pipeline_model.objects.filter(name=NAME, is_active=is_active, **SEEDED_SHAPE).first()


def forwards(apps, schema_editor):
    pipeline_model = apps.get_model("orchestration", "PipelineDefinition")
    if not pipeline_model.objects.filter(name=NAME).exists():
        return
    row = _seeded_row(pipeline_model, is_active=True)
    if row is None:
        logger.info(
            "Kept pipeline definition %r: it no longer carries the seeded shape, "
            "so retiring it would overwrite an operator's decision.",
            NAME,
        )
        return
    row.is_active = False
    row.save(update_fields=["is_active"])


def backwards(apps, schema_editor):
    """Reactivate the row, so long as it is still the one forwards switched off."""
    pipeline_model = apps.get_model("orchestration", "PipelineDefinition")
    row = _seeded_row(pipeline_model, is_active=False)
    if row is None:
        return
    row.is_active = True
    row.save(update_fields=["is_active"])


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0017_seed_routing_table"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
