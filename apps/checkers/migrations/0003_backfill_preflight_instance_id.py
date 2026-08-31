"""Stamp this machine's id onto preflight runs written before the writer fix.

``manage.py preflight`` used to persist ``settings.INSTANCE_ID`` raw, which is
empty on any hub that never set the env var. Those rows never matched the hub's
own ``Node``, which is keyed by ``local_instance_id()``, so the node page said
"No preflight recorded" while the changelist listed the runs. Preflight is
node-local and never pushed, so every blank row on this database was written
here: stamping them with this machine's id is correct, not a guess.
"""

from django.db import migrations

from apps.alerts.identity import local_instance_id


def backfill(apps, schema_editor):
    PreflightRun = apps.get_model("checkers", "PreflightRun")
    PreflightRun.objects.filter(instance_id="").update(instance_id=local_instance_id())


def unbackfill(apps, schema_editor):
    """Not reversible in a meaningful way; a no-op keeps ``migrate`` backwards working."""


class Migration(migrations.Migration):
    dependencies = [
        ("checkers", "0002_preflightrun_preflightcheck"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
