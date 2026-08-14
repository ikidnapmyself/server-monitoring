"""Seed the lane that makes the hub monitor itself.

``bin/install/cron.sh`` installs ``run_pipeline --checks-only --json`` on every
machine, by default every five minutes. Until the commit that adds this
migration, ``checks_only`` terminated the run at CHECKED: the checks ran, the
bridge opened alerts and incidents about the hub's own disk, memory and
temperatures — and the run stopped there. No lane was ever resolved, because
routing was only reached after INGEST. The machine watching eight nodes was the
one machine nobody was watching.

The same commit makes CHECK an entry stage: the entry stage produces an alert,
the lane is resolved from that alert, the lane's stages run. INGEST is the entry
stage for webhook traffic, CHECK for checker-generated traffic. This row is the
route for the second kind. It is not a special case in code — ``origin`` has been
a routing fact since Phase B, and this lane matches on it exactly the way
``cluster-nodes`` matches on ``source``.

``stages`` is EMPTY, and that is the whole design of this row. An empty lane still
routes: it is resolved, stamped on the incident, and its trace_id ties the run to
the alerts it opened — the traceability half of the problem — and then nothing
downstream runs. It is not "no route" (that fails loudly); it is a route that ends
here.

Empty rather than ``["analyze", "notify"]`` because this traffic REPEATS. The cron
fires every five minutes and a still-firing alert is re-reported on every run, so
a hub sitting at 85% disk would produce roughly 288 AI analyses and 288 identical
messages a day about one unchanged problem. The fix for that is de-duplication in
``apps.notify``, which does not exist yet (only the PagerDuty driver has anything
of the sort); until it does, the honest default is to record and correlate without
paging. Note the repetition is what makes this wrong, not the severity — scoping
by severity would not help, because a critical repeats just as hard.

An operator who wants paging adds ``"notify"`` to ``stages`` on this row, which is
exactly the kind of edit the routing table exists to make possible.

``stages`` also never lists ``check``: CHECK is the entry stage and has already run
by the time the lane is resolved; listing it would merely be skipped as
already-succeeded, not re-run. Delete this row entirely and checker-generated
traffic falls through to ``catch-all`` (priority 1000), which DOES list check,
analyze and notify — so deleting this lane makes the hub noisier, not quieter.

Priority 50 for the same reason ``cluster-nodes`` uses it: below the default of
100 that hand-created lanes get, so an operator's own lane does not silently
outrank the hub's self-check, and far below the catch-all.

``get_or_create`` on ``name`` and a shape-matched ``backwards`` follow the 0012
precedent: an install that already configured a lane under this name keeps its
row untouched in both directions. See 0012's docstring for why deleting by name
alone would be destructive.
"""

from django.db import migrations

_LANES = [
    {
        "name": "hub-self-check",
        "description": "This hub's own scheduled checks (bin/install/cron.sh runs "
        "run_pipeline --checks-only, by default every 5 minutes). RECORDS AND "
        "CORRELATES ONLY — it deliberately does not notify. Empty stages means the "
        "run still resolves this lane, stamps it on the incident and carries its "
        "trace_id, so hub self-checks are traceable in the admin like any other "
        "traffic, and then stops. It is empty because this traffic repeats: a "
        "still-firing alert is re-reported every 5 minutes, so listing analyze and "
        "notify would mean ~288 AI calls and ~288 identical messages a day about "
        'one unchanged problem. Add "notify" to stages to page on hub problems '
        '(and "analyze" for an AI summary) — until notification de-duplication '
        "exists, expect one message per cron run for as long as the alert fires.",
        "match": [{"field": "origin", "op": "is", "value": "checker_generated"}],
        "stages": [],
        "priority": 50,
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
    """Delete only rows that still look exactly like what ``forwards`` seeds."""
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for lane in _LANES:
        fields = dict(lane)
        name = fields.pop("name")
        PipelineDefinition.objects.filter(name=name, **fields).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0013_priority_help_text"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
