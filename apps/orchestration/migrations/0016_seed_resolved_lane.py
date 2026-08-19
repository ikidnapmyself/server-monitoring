"""Seed the lane that routes an all-clear straight to notify.

A resolved incident has nothing left to diagnose. Sending it through ANALYZE buys
an LLM call — and a bill on every provider except ``local`` — to explain something
that has already recovered, then says so in a message the operator wanted anyway.
This makes "resolved notifies without analysing" a row rather than a branch in the
orchestrator, so an operator can widen it, re-prioritise it or delete it.

It matches on the ``status`` fact, added to ``facts_from_alert`` in the same
commit: a lane cannot match on a fact nobody produces.

Priority 40 is chosen against the real ordering, not in the abstract. It sits
*above* ``cluster-nodes`` (50, seeded by ``0012``) so that a resolved node alert
takes this lane instead of the node lane's ``["analyze", "notify"]`` — node
traffic is exactly the traffic that resolves most often. It stays below the
default priority of 100 that hand-created lanes get, so an operator lane still
wins if someone wants resolved traffic handled differently.

LIKE EVERY SEEDED ROW, THIS IS NOT A SPECIAL CASE. ``resolve_pipeline`` walks
active lanes by ``(priority, id)`` and knows nothing about "resolved".
``get_or_create`` on ``name`` leaves an existing row exactly as an operator has
it, and ``backwards`` deletes only rows still matching the full seeded shape —
see the note on ``0012.backwards`` for why matching on ``name`` alone would
destroy operator configuration.
"""

from django.db import migrations

_LANE = {
    "name": "resolved-all-clear",
    "description": "Resolved incidents notify without analysis: there is nothing left to "
    "diagnose, and an LLM call on an all-clear is pure cost.",
    "match": [{"field": "status", "op": "is", "value": "resolved"}],
    "stages": ["notify"],
    "priority": 40,
    "is_active": True,
}


def forwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    fields = dict(_LANE)
    name = fields.pop("name")
    PipelineDefinition.objects.get_or_create(name=name, defaults=fields)


def backwards(apps, schema_editor):
    """Delete the row only while it still looks exactly like what forwards seeds."""
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    fields = dict(_LANE)
    name = fields.pop("name")
    PipelineDefinition.objects.filter(name=name, **fields).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0015_stages_help_text"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
