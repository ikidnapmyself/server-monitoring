import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Incident, IncidentStatus, Node
from apps.orchestration.models import PipelineDefinition, PipelineRun, PipelineStatus
from apps.orchestration.testing import clear_lanes


class ReportCommandTests(TestCase):
    def _seed(self):
        # Migration 0012 seeds routing lanes; this file asserts on the exact
        # pipelines list, so start from a table containing only what it creates.
        clear_lanes()
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        pipeline = PipelineDefinition.objects.create(name="report-lane", match=[], priority=1)
        # open incident from web-03, routed via report-lane
        inc_open = Incident.objects.create(
            title="cpu", severity="critical", status=IncidentStatus.OPEN, pipeline=pipeline
        )
        inc_closed = Incident.objects.create(
            title="disk", severity="warning", status=IncidentStatus.RESOLVED, pipeline=pipeline
        )
        for i, inc in enumerate((inc_open, inc_closed)):
            Alert.objects.create(
                fingerprint=f"fp-{i}",
                source="cluster",
                name="a",
                severity="critical",
                started_at=timezone.now(),
                incident=inc,
                node=node,
            )
        PipelineRun.objects.create(trace_id="t", run_id="r", status=PipelineStatus.PENDING)

    def test_json_aggregates(self):
        self._seed()
        out = StringIO()
        call_command("report", "--json", stdout=out)
        data = json.loads(out.getvalue())

        assert data["nodes"] == [{"instance_id": "web-03", "incidents": 2, "open": 1}]
        assert data["pipelines"] == [{"name": "report-lane", "routed": 2}]
        assert data["incidents"]["total"] == 2
        assert data["incidents"]["by_status"] == {"open": 1, "resolved": 1}
        assert data["inbox"] == {"pending": 1, "processing": 0}

    def test_human_output(self):
        self._seed()
        out = StringIO()
        call_command("report", stdout=out)
        text = out.getvalue()
        assert "web-03" in text
        assert "report-lane" in text
        assert "1 pending" in text
        assert "2 total" in text

    def test_empty_report(self):
        clear_lanes()
        out = StringIO()
        call_command("report", stdout=out)
        text = out.getvalue()
        assert "(none)" in text  # no nodes / no pipelines
        assert "Incidents: 0 total (—)" in text
        assert "0 pending, 0 processing" in text
