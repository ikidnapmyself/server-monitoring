"""Tests for the per-incident stage diagnosis classifier."""

from django.test import TestCase

from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Incident
from apps.orchestration.models import PipelineDefinition


class DiagnoseIncidentExpectedStagesTests(TestCase):
    def _statuses(self, incident):
        return {e["stage"]: e["status"] for e in diagnose_incident(incident)}

    def test_returns_four_stages_in_order(self):
        incident = Incident.objects.create(title="Empty")
        stages = [e["stage"] for e in diagnose_incident(incident)]
        self.assertEqual(stages, ["ingest", "check", "analyze", "notify"])

    def test_no_runs_no_pipeline_all_never_ran(self):
        incident = Incident.objects.create(title="No runs")
        self.assertEqual(
            self._statuses(incident),
            {
                "ingest": "never_ran",
                "check": "never_ran",
                "analyze": "never_ran",
                "notify": "never_ran",
            },
        )

    def test_flag_disabled_stage_reads_skipped_config_not_never_ran(self):
        pipe = PipelineDefinition.objects.create(
            name="no-intel",
            run_checkers=True,
            run_intelligence=False,
            run_notify=True,
        )
        incident = Incident.objects.create(title="Routed", pipeline=pipe)
        entries = {e["stage"]: e for e in diagnose_incident(incident)}
        self.assertEqual(entries["analyze"]["status"], "skipped")
        self.assertIn("config", entries["analyze"]["detail"])
        # check + notify remain expected -> never_ran (no executions yet)
        self.assertEqual(entries["check"]["status"], "never_ran")
        self.assertEqual(entries["notify"]["status"], "never_ran")
