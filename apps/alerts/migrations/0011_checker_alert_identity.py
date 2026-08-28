"""Backfill checker-origin alerts onto the instance-keyed identity.

Alerts dedupe on the pair ``(fingerprint, source)``. Checker-origin rows used to
carry two different legacy fingerprints depending on which producer wrote them:
node pushes used ``f"{checker}-{hostname}"`` under source ``cluster``, while the
local bridge used a truncated sha256 of ``f"{checker}:{hostname}"`` under source
``server-checkers``. Both are now ``check:{instance_id}:{checker}`` under source
``cluster`` — hostname is not identity (it collides across stock installs and
changes on rename), the Node primary key is.

Without this backfill the first push after the upgrade would miss the legacy row
on both halves of the dedup pair and open a brand-new Alert (and Incident)
beside it, orphaning the existing history.

Every checker-origin row carries the labels needed to recompute the new
fingerprint, so the rewrite is derived from data already on the row rather than
guessed. Rows with no ``checker`` label are left exactly as they are.
"""

from django.db import migrations

from apps.alerts.identity import local_instance_id, new_fingerprint_for

LEGACY_CHECKER_SOURCES = ["cluster", "server-checkers"]


def forward(apps, schema_editor):
    Alert = apps.get_model("alerts", "Alert")
    fallback_instance_id = local_instance_id()
    seen: set[str] = set()

    queryset = (
        Alert.objects.filter(source__in=LEGACY_CHECKER_SOURCES).order_by("-received_at").iterator()
    )
    for alert in queryset:
        # The hostname label only names the producing machine for ``cluster``
        # rows, which are pushed from that machine and always carry an
        # ``instance_id`` anyway. A ``server-checkers`` row was written by the
        # local bridge, which always ran HERE — but hub-side diagnosis labels
        # those alerts with the subject incident's hostname, a remote machine
        # that never produced them. So for that source the hostname label is
        # ignored and the fallback is this machine's own instance id.
        new = new_fingerprint_for(
            alert.labels,
            fallback_instance_id,
            use_hostname=alert.source == "cluster",
        )
        if new is None:
            continue
        if new in seen:
            # Two legacy rows can collapse onto one identity (the same machine
            # seen by both a push and a local bridge run). The newest keeps the
            # identity; older ones are parked so two histories never silently
            # merge into a single alert.
            new = f"{new}:legacy:{alert.pk}"
        else:
            seen.add(new)
        alert.fingerprint = new
        alert.source = "cluster"
        alert.save(update_fields=["fingerprint", "source"])


def reverse(apps, schema_editor):
    """Deliberate no-op.

    The legacy bridge fingerprint was a truncated sha256 that is not recoverable
    once the row has been rewritten, and the original ``source`` is likewise
    gone. A real rollback of this migration is a database restore.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0010_alert_context_key"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
