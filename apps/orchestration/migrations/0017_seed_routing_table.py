"""Seed the full default routing table, shaped by the hub's active channels.

``0012``/``0014``/``0016`` each seeded one lane on the assumption that a lane may
always list ``notify`` — safe only while ``NotifySelector`` quietly picked the
first active channel by name for any lane that named none. That fallback is now
scoped to interactive callers, so a lane listing ``notify`` with no active channel
fails ``no_channel`` instead of delivering somewhere the operator never chose.

Which makes the seed's job different: it must not manufacture an intent the hub
cannot satisfy. **A channel is optional; a lane that lists ``notify`` is not.** So
this reads how many channels are active and seeds to match — zero means the lanes
omit ``notify`` and the hub records rather than failing, one means they list it
and are bound to it, two or more leaves the binding to an operator because
picking by name is the very bug being removed. See
docs/plans/2026-08-22-lane-channel-required-design.md §2.1.

The body lives in ``apps.orchestration.seeding`` so the tests exercise the same
code the migration runs, rather than a migration body no test ever touches. It
takes the model classes as arguments because a migration must use the historical
models from ``apps.get_model`` — which carry fields but no methods, so the seed
reads ``stages`` directly and never calls ``routable_stages()``.

``get_or_create`` on ``name`` means this is a no-op wherever the earlier
migrations already seeded these lanes, and an operator's edited row is left
exactly as they have it.
"""

from django.db import migrations

from apps.orchestration.seeding import seed_routing_table


def forwards(apps, schema_editor):
    seed_routing_table(
        apps.get_model("orchestration", "PipelineDefinition"),
        apps.get_model("notify", "NotificationChannel"),
    )


def backwards(apps, schema_editor):
    """Deliberately nothing.

    Unlike ``0012``/``0016``, which delete a row still matching their exact seeded
    shape, this migration also *binds* a channel — and neither a lane nor an
    operator's channel choice is this migration's to remove. Reversing it leaves
    the routing table alone; deleting lanes on a downgrade would silence the hub.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0016_seed_resolved_lane"),
        # The seed counts active NotificationChannel rows to decide whether a lane
        # may claim to deliver, so that table must exist before this runs.
        ("notify", "0003_remove_notification_log"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
