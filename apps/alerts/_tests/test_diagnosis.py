"""Tests for the per-incident stage diagnosis classifier."""

from django.test import TestCase

from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Incident
from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution


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


class DiagnoseIncidentStatusTests(TestCase):
    def setUp(self):
        # No pipeline => all four stages expected (un-routed fallback).
        self.incident = Incident.objects.create(title="Statuses")
        self.run = PipelineRun.objects.create(trace_id="t1", run_id="r1", incident=self.incident)

    def _exec(self, stage, status, **kw):
        return StageExecution.objects.create(
            pipeline_run=self.run, stage=stage, status=status, **kw
        )

    def _entry(self, incident, stage):
        return {e["stage"]: e for e in diagnose_incident(incident)}[stage]

    def test_succeeded_with_output_is_ok(self):
        self._exec("notify", "succeeded", output_ref="ref://msg/1")
        self.assertEqual(self._entry(self.incident, "notify")["status"], "ok")

    def test_succeeded_with_snapshot_is_ok(self):
        self._exec("notify", "succeeded", output_snapshot={"sent": 1})
        self.assertEqual(self._entry(self.incident, "notify")["status"], "ok")

    def test_succeeded_no_output_is_empty(self):
        self._exec("notify", "succeeded")
        self.assertEqual(self._entry(self.incident, "notify")["status"], "empty")

    def test_succeeded_but_run_level_ref_present_is_ok(self):
        self.run.notify_output_ref = "ref://delivery/1"
        self.run.save(update_fields=["notify_output_ref"])
        self._exec("notify", "succeeded")
        self.assertEqual(self._entry(self.incident, "notify")["status"], "ok")

    def test_failed_reports_error_and_retryable(self):
        self._exec(
            "analyze",
            "failed",
            error_type="Timeout",
            error_message="provider 504",
            error_retryable=True,
        )
        entry = self._entry(self.incident, "analyze")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("provider 504", entry["detail"])
        self.assertIn("retryable=True", entry["detail"])

    def test_running_is_stalled(self):
        self._exec("check", "running")
        self.assertEqual(self._entry(self.incident, "check")["status"], "stalled")

    def test_pending_is_stalled(self):
        self._exec("check", "pending")
        self.assertEqual(self._entry(self.incident, "check")["status"], "stalled")

    def test_explicit_skipped_execution_reports_reason(self):
        self._exec("check", "skipped", error_message="Skipped: diagnostics inline")
        entry = self._entry(self.incident, "check")
        self.assertEqual(entry["status"], "skipped")
        self.assertEqual(entry["detail"], "diagnostics inline")

    def test_explicit_skipped_without_reason(self):
        self._exec("check", "skipped", error_message="")
        self.assertEqual(self._entry(self.incident, "check")["detail"], "no reason recorded")


class DiagnoseIncidentAggregationTests(TestCase):
    def _entry(self, incident, stage):
        return {e["stage"]: e for e in diagnose_incident(incident)}[stage]

    def test_latest_run_wins_and_rollup_counts(self):
        incident = Incident.objects.create(title="Multi")
        # Older run: notify succeeded. Newer run: notify failed.
        old = PipelineRun.objects.create(trace_id="t1", run_id="r1", incident=incident)
        StageExecution.objects.create(
            pipeline_run=old, stage="notify", status="succeeded", output_ref="ref://1"
        )
        new = PipelineRun.objects.create(trace_id="t2", run_id="r2", incident=incident)
        StageExecution.objects.create(
            pipeline_run=new,
            stage="notify",
            status="failed",
            error_type="X",
            error_message="boom",
            error_retryable=False,
        )
        entry = self._entry(incident, "notify")
        self.assertEqual(entry["status"], "failed")  # latest run wins
        self.assertEqual(entry["runs"], "succeeded in 1/2 runs")

    def test_highest_attempt_within_latest_run_wins(self):
        incident = Incident.objects.create(title="Retry")
        run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
        StageExecution.objects.create(
            pipeline_run=run,
            stage="analyze",
            status="failed",
            attempt=1,
            error_type="X",
            error_message="first",
            error_retryable=True,
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage="analyze",
            status="succeeded",
            attempt=2,
            output_ref="ref://ok",
        )
        self.assertEqual(self._entry(incident, "analyze")["status"], "ok")

    def test_ingest_succeeded_without_output_is_empty(self):
        # ingest has no PipelineRun output-ref attr (run_ref_attr is None),
        # so emptiness rests solely on the execution's own snapshot/output_ref.
        incident = Incident.objects.create(title="IngestEmpty")
        run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
        StageExecution.objects.create(pipeline_run=run, stage="ingest", status="succeeded")
        self.assertEqual(self._entry(incident, "ingest")["status"], "empty")

    def test_unknown_status_falls_back_to_never_ran(self):
        # A status outside the known vocabulary matches no classify branch and
        # leaves the default never_ran verdict untouched (defensive fall-through).
        incident = Incident.objects.create(title="Unknown")
        run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
        StageExecution.objects.create(pipeline_run=run, stage="notify", status="cancelled")
        self.assertEqual(self._entry(incident, "notify")["status"], "never_ran")
