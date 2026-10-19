"""Reword the retired ``hub-self-check`` route's description for the console editor.

``0014`` seeded a description written for the admin: it named ``run_pipeline`` and
``trace_id`` and explained a record-only design that ``0018`` retired. The console shows
descriptions as plain words, so the row gets one that says what is true now.

Only a row still carrying the exact ``0014`` text is touched. Any other description is the
operator's. Reverse is a no-op: the old text described a lane that no longer routes.
"""

from django.db import migrations

NAME = "hub-self-check"

#: Frozen copy of the description ``0014`` seeded.
SEEDED = (
    "This hub's own scheduled checks (bin/install/cron.sh runs "
    "run_pipeline --checks-only, by default every 5 minutes). RECORDS AND "
    "CORRELATES ONLY — it deliberately does not notify. Empty stages means the "
    "run still resolves this lane, stamps it on the incident and carries its "
    "trace_id, so hub self-checks are traceable in the admin like any other "
    "traffic, and then stops. It is empty because this traffic repeats: a "
    "still-firing alert is re-reported every 5 minutes, so listing analyze and "
    "notify would mean ~288 AI calls and ~288 identical messages a day about "
    'one unchanged problem. Add "notify" to stages to page on hub problems '
    '(and "analyze" for an AI summary) — until notification de-duplication '
    "exists, expect one message per cron run for as long as the alert fires."
)

PLAIN = (
    "Retired. This hub's own checks now route like any node's, so the route that "
    "handles pushed node alerts handles them too."
)


def forwards(apps, schema_editor):
    pipeline_model = apps.get_model("orchestration", "PipelineDefinition")
    pipeline_model.objects.filter(name=NAME, description=SEEDED).update(description=PLAIN)


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0019_pipelinedefinition_version_help"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
