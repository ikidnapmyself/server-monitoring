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

A row parked under a ``:legacy:<pk>`` fingerprint is also resolved: that key is
one no producer emits, so nothing would ever look the row up again to close it,
and its incident would stay open for good.

Every checker-origin row carries the labels needed to recompute the new
fingerprint, so the rewrite is derived from data already on the row rather than
guessed. Rows with no ``checker`` label are left exactly as they are.
"""

import socket

from django.conf import settings
from django.db import migrations
from django.utils import timezone

LEGACY_CHECKER_SOURCES = ["cluster", "server-checkers"]


# DELIBERATE DUPLICATION of ``apps.alerts.identity``
# --------------------------------------------------
# The two helpers below are frozen copies of that module as it stood on
# 2026-08-28, and must stay frozen. This migration used to import the live
# module, which means replaying it on a fresh database would run whatever the
# module had become in the meantime: change the fingerprint format next year and
# fresh installs get the new one while every upgraded install keeps the old, from
# the same migration number. A migration is a historical snapshot of a schema
# *and* of the data it writes, so it carries its own copy and must never be
# re-pointed at the live module. ``apps.alerts.identity`` stays the runtime
# version — edit it freely; this snapshot does not move.
# Same reasoning, same shape as ``0017_seed_routing_table`` in apps.orchestration.


def _local_instance_id() -> str:
    """Frozen copy of ``apps.alerts.identity.local_instance_id``."""
    return getattr(settings, "INSTANCE_ID", "") or socket.gethostname()


def _checker_fingerprint(instance_id: str, checker_name: str) -> str:
    """Frozen copy of ``apps.alerts.identity.checker_fingerprint``."""
    return f"check:{instance_id}:{checker_name}"


def _new_fingerprint_for(labels, fallback_instance_id: str, use_hostname: bool = True):
    """Frozen copy of ``apps.alerts.identity.new_fingerprint_for``."""
    if not isinstance(labels, dict):
        return None
    checker = labels.get("checker")
    if not checker:
        return None
    instance = labels.get("instance_id")
    if not instance and use_hostname:
        instance = labels.get("hostname")
    return _checker_fingerprint(instance or fallback_instance_id, checker)


def forward(apps, schema_editor):
    Alert = apps.get_model("alerts", "Alert")
    fallback_instance_id = _local_instance_id()
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
        new = _new_fingerprint_for(
            alert.labels,
            fallback_instance_id,
            use_hostname=alert.source == "cluster",
        )
        if new is None:
            continue
        fields = ["fingerprint", "source"]
        if new in seen:
            # Two legacy rows can collapse onto one identity (the same machine
            # seen by both a push and a local bridge run). The newest keeps the
            # identity; older ones are parked so two histories never silently
            # merge into a single alert.
            new = f"{new}:legacy:{alert.pk}"
            # A parked fingerprint is a value no producer will ever emit again, so
            # ``_process_alert``'s ``(fingerprint, source)`` lookup can never find
            # this row to resolve it. Left firing it would hold its incident open
            # forever on every machine that had both a push row and a bridge row.
            # Closing it here is the only chance; ``_check_incident_resolution``
            # then closes the incident once all its alerts are resolved.
            # Values are set directly: a historical model carries fields, not the
            # AlertStatus choices class or ``resolve()``.
            alert.status = "resolved"
            alert.ended_at = timezone.now()
            fields += ["status", "ended_at"]
        else:
            seen.add(new)
        alert.fingerprint = new
        alert.source = "cluster"
        alert.save(update_fields=fields)


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
