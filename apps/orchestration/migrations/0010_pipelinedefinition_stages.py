"""Replace the three ``run_*`` booleans with one ordered ``stages`` list.

The operation order below is load-bearing and hand-written (``makemigrations``
emits the removals first). ``stages`` must be added and backfilled *while the
booleans still exist*, because the backfill reads them; only then may they be
dropped.

Django reverses operations in reverse order, so the backwards path re-adds the
three booleans, runs ``backwards()`` to repopulate them from ``stages``, and only
then drops ``stages`` — which is exactly right.
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for defn in PipelineDefinition.objects.all():
        stages = []
        if defn.run_checkers:
            stages.append("check")
        if defn.run_intelligence:
            stages.append("analyze")
        if defn.run_notify:
            stages.append("notify")
        defn.stages = stages
        defn.save(update_fields=["stages"])


def backwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for defn in PipelineDefinition.objects.all():
        stages = defn.stages or []
        defn.run_checkers = "check" in stages
        defn.run_intelligence = "analyze" in stages
        defn.run_notify = "notify" in stages
        defn.save(update_fields=["run_checkers", "run_intelligence", "run_notify"])


class Migration(migrations.Migration):

    dependencies = [
        ("orchestration", "0009_alter_pipelinerun_node"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelinedefinition",
            name="stages",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Ordered downstream stages, e.g. ["check", "analyze", "notify"]. '
                    "The entry stage (ingest) is not listed: it has already run by the "
                    "time this lane is resolved. Checker-generated runs do not consult "
                    "a lane at all today."
                ),
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name="pipelinedefinition",
            name="run_checkers",
        ),
        migrations.RemoveField(
            model_name="pipelinedefinition",
            name="run_intelligence",
        ),
        migrations.RemoveField(
            model_name="pipelinedefinition",
            name="run_notify",
        ),
    ]
