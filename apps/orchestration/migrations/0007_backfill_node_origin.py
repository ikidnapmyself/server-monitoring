"""Backfill node + origin on pre-existing PipelineRun rows.

Rows created before Phase 2 have the ``origin`` default (incoming_webhook) and a
NULL ``node``. Derive ``origin`` from ``source`` (``cli*`` -> manual, else
incoming_webhook) and derive ``node`` from the linked incident's alerts — the FK
to a node lives on ``Alert``, not ``Incident`` — using the first alert that
carries one. Reversible with a no-op reverse (the backfilled values are harmless
to keep). Updates are applied in chunked ``bulk_update`` batches.
"""

from django.db import migrations

_BATCH_SIZE = 500


def forwards(apps, schema_editor):
    PipelineRun = apps.get_model("orchestration", "PipelineRun")
    Alert = apps.get_model("alerts", "Alert")

    batch = []
    for run in PipelineRun.objects.select_related("incident").all().iterator():
        src = (run.source or "").lower()
        run.origin = "manual" if src.startswith("cli") else "incoming_webhook"
        if run.incident_id:
            node_id = (
                Alert.objects.filter(incident_id=run.incident_id, node__isnull=False)
                .values_list("node_id", flat=True)
                .first()
            )
            if node_id:
                run.node_id = node_id
        batch.append(run)
        if len(batch) >= _BATCH_SIZE:
            PipelineRun.objects.bulk_update(batch, ["origin", "node"], batch_size=_BATCH_SIZE)
            batch = []

    if batch:
        PipelineRun.objects.bulk_update(batch, ["origin", "node"], batch_size=_BATCH_SIZE)


def reverse(apps, schema_editor):
    """No-op: backfilled node/origin values are safe to retain."""


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0006_pipelinerun_node_pipelinerun_origin"),
    ]

    operations = [
        migrations.RunPython(forwards, reverse),
    ]
