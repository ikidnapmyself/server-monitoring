from django.db import migrations, models
from django.db.models import F


def backfill_opened_at(apps, schema_editor):
    Incident = apps.get_model("alerts", "Incident")
    Incident.objects.update(opened_at=F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0013_drop_incident_title_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="incident",
            name="opened_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(backfill_opened_at, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="incident",
            name="opened_at",
            field=models.DateTimeField(
                help_text="When the current episode began: creation, or the latest reopen."
            ),
        ),
        migrations.AddIndex(
            model_name="incident",
            index=models.Index(fields=["opened_at"], name="alerts_inci_opened__252829_idx"),
        ),
    ]
