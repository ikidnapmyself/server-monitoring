"""Backfill node + origin on pre-existing PipelineRun rows.

Rows created before Phase 2 have the ``origin`` default (incoming_webhook) and a
NULL ``node``. Derive ``origin`` from ``source`` (``cli*`` -> manual, else
incoming_webhook) and copy ``node`` from the linked incident when it carries one.
Reversible with a no-op reverse (the backfilled values are harmless to keep).
"""

from django.db import migrations


def forwards(apps, schema_editor):
    PipelineRun = apps.get_model("orchestration", "PipelineRun")
    for run in PipelineRun.objects.select_related("incident").all().iterator():
        src = (run.source or "").lower()
        run.origin = "manual" if src.startswith("cli") else "incoming_webhook"
        if run.incident_id and getattr(run.incident, "node_id", None):
            run.node_id = run.incident.node_id
        run.save(update_fields=["origin", "node"])


def reverse(apps, schema_editor):
    """No-op: backfilled node/origin values are safe to retain."""


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0006_pipelinerun_node_pipelinerun_origin"),
    ]

    operations = [
        migrations.RunPython(forwards, reverse),
    ]
