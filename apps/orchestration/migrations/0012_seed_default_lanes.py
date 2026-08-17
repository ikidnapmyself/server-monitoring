"""Seed the two lanes that replace the orchestrator's implicit routing fallback.

Until now an alert that matched no ``PipelineDefinition`` fell through to a
default order hard-coded in Python (``_downstream_stages_or_default``): CHECK ->
ANALYZE -> NOTIFY, with CHECK dropped when ``skip_checkers`` was set. That
behaviour had no configuration behind it. It was invisible in the database,
un-editable by an operator, and unrepresentable on any map drawn from
``PipelineDefinition`` rows — the routing table said one thing and the pipeline
did another.

This migration moves that behaviour into two rows and the same commit deletes the
Python fallback, so a fresh install behaves exactly as it did before while every
routing decision is now readable from the table.

THESE ROWS ARE NOT SPECIAL CASES. The routing engine knows nothing about
"cluster" or "catch-all": ``resolve_pipeline`` walks active lanes by
``(priority, id)`` and takes the first whose ``match`` passes, and an empty
``match`` is a catch-all like any other. Both rows are as editable, re-prioritisable
and deletable as anything an operator creates — deleting the catch-all is a
supported choice, and it means unmatched traffic now fails loudly as ``no_route``
rather than silently taking a route nobody configured.

``get_or_create`` on ``name`` keeps this safe on an install that already
configured a lane under either name: an existing row is left exactly as the
operator has it, and ``backwards`` leaves that adopted row alone too (it deletes
only rows still matching the full seeded shape — see the note there).

Priorities are chosen against the real ordering, not in the abstract:
``cluster-nodes`` sits at 50 so it beats the default priority of 100 that
hand-created lanes get, and the catch-all sits at 1000 so it loses to everything
and only fires when nothing else claimed the alert. On a database whose existing
lanes already carry an empty ``match`` at priority 100, that older catch-all still
wins and the seeded one never runs — inert, but correct, and it is what makes the
fallback deletion safe on a fresh install.

One collision this seed CAUSES, and the fix that absorbs it: ``setup_cluster``
also wants a catch-all to hang its notification channel on, and it used to create
``default-catch-all`` at priority 1000 — a tie with the row seeded here, broken on
``id``, and ``migrate`` always runs first. Its lane would therefore never run
while holding the only configured channel. ``_bind_catchall_pipeline`` now binds
the lane that actually wins rather than one it owns by name, which is
self-correcting for operator catch-alls as well.
"""

from django.db import migrations

_LANES = [
    {
        "name": "cluster-nodes",
        "description": "Alerts pushed by a node. CHECK is omitted: the node already ran "
        "its own checkers, so hub-side checks would report the hub's CPU and disk.",
        "match": [{"field": "source", "op": "is", "value": "cluster"}],
        "stages": ["analyze", "notify"],
        "priority": 50,
        "is_active": True,
    },
    {
        "name": "catch-all",
        "description": "Everything else. Replaces the implicit fallback that used to live "
        "in the orchestrator, as a visible, editable row.",
        "match": [],
        "stages": ["check", "analyze", "notify"],
        "priority": 1000,
        "is_active": True,
    },
]


def forwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for lane in _LANES:
        fields = dict(lane)
        name = fields.pop("name")
        PipelineDefinition.objects.get_or_create(name=name, defaults=fields)


def backwards(apps, schema_editor):
    """Delete only rows that still look exactly like what ``forwards`` seeds.

    Matching on ``name`` alone would destroy operator configuration: forwards
    adopts a pre-existing ``catch-all`` untouched, so a rollback during a bad
    deploy (``migrate orchestration 0011``) would delete that operator's lane and
    its channel FK with it, the hub would go quiet, and re-applying forwards would
    seed a *different* lane rather than restore the original. Matching the full
    shape means an adopted or since-edited row is left alone — the cost is that a
    row an operator edited back into the exact seeded shape is indistinguishable
    from the seed, which is the harmless direction to be wrong in.
    """
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for lane in _LANES:
        fields = dict(lane)
        name = fields.pop("name")
        PipelineDefinition.objects.filter(name=name, **fields).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0011_pipelinedefinition_channel"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
