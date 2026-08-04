"""Operational read model: incidents per node, routing hits per pipeline, inbox depth.

python manage.py report
python manage.py report --json
"""

import json

from django.core.management.base import BaseCommand
from django.db.models import Count, Q


class Command(BaseCommand):
    help = "Report incidents per node, routing hits per pipeline, and inbox depth."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        data = self._collect()
        if options["as_json"]:
            self.stdout.write(json.dumps(data, indent=2, default=str))
            return
        self._render(data)

    def _collect(self) -> dict:
        from apps.alerts.models import Incident, IncidentStatus, Node
        from apps.orchestration.models import PipelineDefinition, PipelineRun, PipelineStatus

        nodes = [
            {"instance_id": n.instance_id, "incidents": n.incidents, "open": n.open_incidents}
            for n in Node.objects.annotate(
                incidents=Count("alerts__incident", distinct=True),
                open_incidents=Count(
                    "alerts__incident",
                    filter=Q(alerts__incident__status=IncidentStatus.OPEN),
                    distinct=True,
                ),
            ).order_by("instance_id")
        ]

        pipelines = [
            {"name": p.name, "routed": p.routed}
            for p in PipelineDefinition.objects.annotate(routed=Count("incidents")).order_by(
                "priority", "name"
            )
        ]

        by_status = {
            row["status"]: row["n"]
            for row in Incident.objects.values("status").annotate(n=Count("id"))
        }

        return {
            "nodes": nodes,
            "pipelines": pipelines,
            "incidents": {"total": sum(by_status.values()), "by_status": by_status},
            "inbox": {
                "pending": PipelineRun.objects.filter(status=PipelineStatus.PENDING).count(),
                "processing": PipelineRun.objects.filter(status=PipelineStatus.PROCESSING).count(),
            },
        }

    def _render(self, data: dict) -> None:
        self.stdout.write(self.style.HTTP_INFO("=== Report ==="))

        self.stdout.write("Nodes:")
        if data["nodes"]:
            for n in data["nodes"]:
                self.stdout.write(
                    f"  {n['instance_id']:<20} {n['incidents']} incident(s) ({n['open']} open)"
                )
        else:
            self.stdout.write("  (none)")

        self.stdout.write("Pipelines:")
        if data["pipelines"]:
            for p in data["pipelines"]:
                self.stdout.write(f"  {p['name']:<24} {p['routed']} routed")
        else:
            self.stdout.write("  (none)")

        inc = data["incidents"]
        by_status = ", ".join(f"{k}: {v}" for k, v in sorted(inc["by_status"].items())) or "—"
        self.stdout.write(f"Incidents: {inc['total']} total ({by_status})")

        ib = data["inbox"]
        self.stdout.write(f"Inbox: {ib['pending']} pending, {ib['processing']} processing")
