import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Incident
from apps.orchestration.models import PipelineDefinition, PipelineRun, PipelineStatus


class TraceCommandTests(TestCase):
    def _chain(self, *, handled=True, with_pipeline=True):
        pipeline = None
        if with_pipeline:
            pipeline = PipelineDefinition.objects.create(name="trace-lane", match=[], priority=3)
        incident = Incident.objects.create(title="High CPU", severity="critical", pipeline=pipeline)
        alert = Alert.objects.create(
            fingerprint="fp",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
            trace_id="tr-1",
        )
        if handled:
            run = PipelineRun.objects.create(
                trace_id="tr-1", run_id="run-1", status=PipelineStatus.NOTIFIED, incident=incident
            )
            run.stage_executions.create(stage="ingest", status="succeeded", attempt=1)
        return alert, incident

    def test_by_alert_id_renders_chain(self):
        alert, _ = self._chain()
        out = StringIO()
        call_command("trace", str(alert.id), stdout=out)
        text = out.getvalue()
        self.assertIn("Alert #", text)
        self.assertIn("run-1", text)
        self.assertIn("ingest", text)
        self.assertIn("handled by pipeline", text)

    def test_by_trace_id(self):
        self._chain()
        out = StringIO()
        call_command("trace", "tr-1", stdout=out)
        self.assertIn("Incident #", out.getvalue())
        self.assertIn("run-1", out.getvalue())

    def test_json_output(self):
        alert, _ = self._chain()
        out = StringIO()
        call_command("trace", str(alert.id), "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data["trace_id"], "tr-1")
        self.assertTrue(data["handled"])
        self.assertEqual(data["runs"][0]["run_id"], "run-1")
        self.assertEqual(data["pipeline"]["name"], "trace-lane")

    def test_unhandled_alert_shows_inbox(self):
        alert, _ = self._chain(handled=False)
        out = StringIO()
        call_command("trace", str(alert.id), stdout=out)
        self.assertIn("inbox — not processed", out.getvalue())

    def test_handled_without_pipeline_stamp(self):
        # A run exists but the incident was never stamped with a pipeline.
        alert, _ = self._chain(handled=True, with_pipeline=False)
        out = StringIO()
        call_command("trace", str(alert.id), stdout=out)
        self.assertIn("handled (run run-1)", out.getvalue())

    def test_alert_without_trace_uses_incident_runs(self):
        # Alert has no trace_id → runs are found via the incident, and trace shows "—".
        incident = Incident.objects.create(title="x", severity="warning")
        alert = Alert.objects.create(
            fingerprint="fp0",
            source="cluster",
            name="cpu",
            severity="warning",
            started_at=timezone.now(),
            incident=incident,
            trace_id="",
        )
        PipelineRun.objects.create(
            trace_id="", run_id="run-x", status=PipelineStatus.NOTIFIED, incident=incident
        )
        out = StringIO()
        call_command("trace", str(alert.id), stdout=out)
        text = out.getvalue()
        self.assertIn("trace_id: —", text)
        self.assertIn("run-x", text)

    def test_trace_with_run_but_no_incident(self):
        # A run with no incident (rare) still renders without an Incident line.
        PipelineRun.objects.create(trace_id="orphan", run_id="run-o", status=PipelineStatus.CHECKED)
        out = StringIO()
        call_command("trace", "orphan", stdout=out)
        text = out.getvalue()
        self.assertNotIn("Incident #", text)
        self.assertIn("run-o", text)

    def test_unknown_alert_id_errors(self):
        with self.assertRaises(CommandError):
            call_command("trace", "999999")

    def test_unknown_trace_errors(self):
        with self.assertRaises(CommandError):
            call_command("trace", "no-such-trace")
