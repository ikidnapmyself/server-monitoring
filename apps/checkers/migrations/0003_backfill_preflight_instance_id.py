"""Stamp this machine's id onto preflight runs written before the writer fix.

``manage.py preflight`` used to persist ``settings.INSTANCE_ID`` raw, which is
empty on any hub that never set the env var. Those rows never matched the hub's
own ``Node``, which is keyed by ``local_instance_id()``, so the node page said
"No preflight recorded" while the changelist listed the runs. Preflight is
node-local and never pushed, so every blank row on this database was written
here: stamping them with this machine's id is correct, not a guess.
"""

import socket

from django.conf import settings
from django.db import migrations


# DELIBERATE DUPLICATION of ``apps.alerts.identity``
# --------------------------------------------------
# The helper below is a frozen copy of that module as it stood on 2026-08-31,
# and must stay frozen. This migration used to import the live module, which
# means replaying it on a fresh database would run whatever the module had
# become in the meantime: change how a machine names itself next year and fresh
# installs stamp the new id while every upgraded install keeps the old, from the
# same migration number. A migration is a historical snapshot of a schema *and*
# of the data it writes, so it carries its own copy and must never be re-pointed
# at the live module. ``apps.alerts.identity`` stays the runtime version — edit
# it freely; this snapshot does not move.
# Same reasoning, same shape as ``0011_checker_alert_identity`` in apps.alerts
# and ``0017_seed_routing_table`` in apps.orchestration.


def _local_instance_id() -> str:
    """Frozen copy of ``apps.alerts.identity.local_instance_id``."""
    return getattr(settings, "INSTANCE_ID", "") or socket.gethostname()


def backfill(apps, schema_editor):
    PreflightRun = apps.get_model("checkers", "PreflightRun")
    PreflightRun.objects.filter(instance_id="").update(instance_id=_local_instance_id())


def unbackfill(apps, schema_editor):
    """Not reversible in a meaningful way; a no-op keeps ``migrate`` backwards working."""


class Migration(migrations.Migration):
    dependencies = [
        ("checkers", "0002_preflightrun_preflightcheck"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
