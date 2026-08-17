"""Replace the ``channels`` M2M with a single ``channel`` FK.

Delivery only ever notified one channel — the alphabetically first *active* one
(``NotifyExecutor._route_incident``). The M2M read as fan-out but never fanned
out. The FK makes the field match the behaviour.

THIS MIGRATION IS LOSSY. A lane wired to several channels keeps exactly one (the
same one delivery already picked) and the remaining rows are discarded when the
join table is dropped. No behaviour changes — the discarded channels were never
consulted — but the data is gone, and ``backwards()`` cannot bring it back: it
restores the single surviving channel only. A lane whose channels are all
inactive keeps none, because delivery selected none. ``forwards()`` logs a warning
naming every lane and every channel it drops, so the loss is auditable in the
migrate log and not just described here.

Between ``AddField`` and ``RemoveField`` both ``channel`` and ``channels`` claim
``related_name="pipelines"``. That is harmless here — historical models skip system
checks, and both functions below use only forward accessors — but a data migration
inserted between those two operations that reached through ``channel.pipelines``
would bind to whichever descriptor registered last. Add such a step before the
``RemoveField``, not between it and the ``AddField``.

The operation order below is load-bearing and hand-written: ``channel`` must be
added and backfilled *while the M2M still exists*, because the backfill reads it;
only then may ``channels`` be dropped. Django reverses operations in reverse
order, so the backwards path re-adds the M2M, runs ``backwards()`` to repopulate
it, and only then drops ``channel`` — which is exactly right.
"""

import logging

from django.db import migrations, models
import django.db.models.deletion

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    # order_by makes the warning block reproducible in a production migrate log --
    # the entire reason for logging it -- and prefetch drops the per-lane channel
    # reads from 2 queries per lane to 2 in total.
    lanes = PipelineDefinition.objects.order_by("name").prefetch_related("channels")
    for defn in lanes:
        # Mirror exactly what delivery selected before this migration.
        chosen = defn.channels.filter(is_active=True).order_by("name").first()
        if chosen is not None:
            defn.channel_id = chosen.id
            defn.save(update_fields=["channel"])
        # Name every channel this lane loses. Silent row deletion is exactly the
        # kind of invisible config change this migration exists to stamp out, so
        # the discard is auditable in the migrate log rather than only in a
        # docstring an operator will never read.
        survivor_id = chosen.id if chosen is not None else None
        dropped = sorted(c.name for c in defn.channels.all() if c.id != survivor_id)
        if not dropped:
            continue
        names = ", ".join(repr(n) for n in dropped)
        if chosen is None:
            # Nothing survives, so "unused" would be wrong -- there is no survivor to
            # be unused relative to. This lane simply goes dark.
            logger.warning(
                "Pipeline lane %r: keeping no channel (none were active), dropping "
                "%d channel(s): %s. This lane notified nothing before and notifies "
                "nothing now; reversing this migration cannot restore these rows.",
                defn.name,
                len(dropped),
                names,
            )
        else:
            logger.warning(
                "Pipeline lane %r: keeping channel %r, dropping %d unused channel(s): %s. "
                "These were never notified (delivery only used the first active channel) "
                "and cannot be restored by reversing this migration.",
                defn.name,
                chosen.name,
                len(dropped),
                names,
            )


def backwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for defn in PipelineDefinition.objects.all():
        if defn.channel_id:
            defn.channels.set([defn.channel_id])


class Migration(migrations.Migration):

    dependencies = [
        ("notify", "0003_remove_notification_log"),
        ("orchestration", "0010_pipelinedefinition_stages"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelinedefinition",
            name="channel",
            field=models.ForeignKey(
                blank=True,
                help_text="Channel this lane notifies. One channel: delivery never fanned out.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pipelines",
                to="notify.notificationchannel",
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name="pipelinedefinition",
            name="channels",
        ),
    ]
