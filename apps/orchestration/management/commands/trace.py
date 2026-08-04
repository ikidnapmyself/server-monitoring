"""Trace an alert's journey: Alert -> Incident -> PipelineRun -> StageExecution.

python manage.py trace 42            # by Alert id
python manage.py trace <trace_id>    # by correlation id
python manage.py trace 42 --json
"""

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Trace an alert's journey (by Alert id or trace_id)."

    def add_arguments(self, parser):
        parser.add_argument("target", help="Alert id (int) or a trace_id.")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        chain = self._resolve(options["target"])
        if options["as_json"]:
            self.stdout.write(json.dumps(chain, indent=2, default=str))
            return
        self._render(chain)

    def _resolve(self, target: str) -> dict:
        from apps.alerts.models import Alert, Incident
        from apps.orchestration.models import PipelineRun

        alert = None
        trace_id = target
        # An integer target is an Alert id; otherwise treat it as a trace_id.
        if target.isdigit():
            alert = Alert.objects.filter(id=int(target)).select_related("incident").first()
            if alert is None:
                raise CommandError(f"No alert with id={target}")
            trace_id = alert.trace_id

        incident = alert.incident if alert else None
        runs = list(PipelineRun.objects.filter(trace_id=trace_id).order_by("created_at"))
        # Fall back to the incident's runs when the alert had no trace_id.
        if not runs and incident is not None:
            runs = list(incident.pipeline_runs.all().order_by("created_at"))
        # Resolve incident from a run when starting from a bare trace_id.
        if incident is None and runs and runs[0].incident_id:
            incident = Incident.objects.filter(id=runs[0].incident_id).first()

        if alert is None and not runs and incident is None:
            raise CommandError(f"Nothing found for '{target}' (no alert, trace, or run).")

        pipeline = incident.pipeline if incident else None

        return {
            "alert": (
                {"id": alert.id, "name": alert.name, "source": alert.source} if alert else None
            ),
            "trace_id": trace_id,
            "incident": (
                {"id": incident.id, "title": incident.title, "status": incident.status}
                if incident
                else None
            ),
            "pipeline": (
                {"name": pipeline.name, "priority": pipeline.priority} if pipeline else None
            ),
            "runs": [
                {
                    "run_id": r.run_id,
                    "status": r.status,
                    "stages": list(
                        r.stage_executions.order_by("started_at").values_list("stage", "status")
                    ),
                }
                for r in runs
            ],
            "handled": bool(runs),
        }

    def _render(self, chain: dict) -> None:
        if chain["alert"]:
            a = chain["alert"]
            self.stdout.write(f"Alert #{a['id']}: {a['name']} (source={a['source']})")
        self.stdout.write(f"trace_id: {chain['trace_id'] or '—'}")
        if chain["incident"]:
            inc = chain["incident"]
            self.stdout.write(f"Incident #{inc['id']}: {inc['title']} [{inc['status']}]")
        if chain["pipeline"]:
            p = chain["pipeline"]
            self.stdout.write(f"Routed by: {p['name']} (priority {p['priority']})")

        if not chain["handled"]:
            self.stdout.write(self.style.WARNING("inbox — not processed (no pipeline run)"))
            return

        for r in chain["runs"]:
            self.stdout.write(f"Run {r['run_id']} — {r['status']}")
            for stage, status in r["stages"]:
                self.stdout.write(f"  - {stage}: {status}")
        last = chain["runs"][-1]
        if chain["pipeline"]:
            self.stdout.write(
                self.style.SUCCESS(f"handled by pipeline '{chain['pipeline']['name']}'")
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"handled (run {last['run_id']})"))
