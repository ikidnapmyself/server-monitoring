"""Tests for the per-incident stage diagnosis classifier."""

from django.test import TestCase

from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Incident
from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution


class DiagnoseIncidentExpectedStagesTests(TestCase):
    def _statuses(self, incident):
        return {e["stage"]: e["status"] for e in diagnose_incident(incident)}

    def test_returns_the_produced_stages_in_order(self):
        # INGEST is history and this incident has no legacy run — see
        # DiagnoseIncidentIngestIsHistoryTests.
        incident = Incident.objects.create(title="Empty")
        stages = [e["stage"] for e in diagnose_incident(incident)]
        self.assertEqual(stages, ["check", "analyze", "notify"])

    def test_no_runs_no_pipeline_all_never_ran(self):
        incident = Incident.objects.create(title="No runs")
        self.assertEqual(
            self._statuses(incident),
            {
                "check": "never_ran",
                "analyze": "never_ran",
                "notify": "never_ran",
            },
        )

    def test_unlisted_stage_reads_skipped_config_not_never_ran(self):
        pipe = PipelineDefinition.objects.create(
            name="no-intel",
            stages=["check", "notify"],
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


class DiagnoseIncidentJunkPipelineTests(TestCase):
    """Diagnosis reads the lane through the same normalisation the orchestrator uses."""

    def test_bare_string_stages_does_not_substring_match(self):
        """A junk row storing "notify" must not make every stage look expected.

        ``"check" in "notify"`` is False but ``"notify" in "notify"`` is True, so a raw
        membership test on a string column would report NOTIFY as expected while the
        orchestrator (which normalises) would never run it.
        """
        pipe = PipelineDefinition.objects.create(name="junk-lane", stages="notify")
        incident = Incident.objects.create(title="Junk", pipeline=pipe)
        entries = {e["stage"]: e for e in diagnose_incident(incident)}
        self.assertEqual(entries["notify"]["status"], "skipped")
        self.assertEqual(entries["check"]["status"], "skipped")

    def test_unknown_stage_in_list_is_ignored(self):
        pipe = PipelineDefinition.objects.create(name="part-junk", stages=["sparkle", "notify"])
        incident = Incident.objects.create(title="Part junk", pipeline=pipe)
        entries = {e["stage"]: e for e in diagnose_incident(incident)}
        self.assertEqual(entries["notify"]["status"], "never_ran")  # expected, not yet run
        self.assertEqual(entries["analyze"]["status"], "skipped")


class DiagnoseIncidentIngestIsHistoryTests(TestCase):
    """INGEST is reported only for an incident that actually has a legacy run.

    Since "a run is an incident", no producer records an INGEST stage. Reporting
    it for a new incident would show a permanent gap for a stage that will never
    run.
    """

    def _stages(self, incident):
        return [e["stage"] for e in diagnose_incident(incident)]

    def _entries(self, incident):
        return {e["stage"]: e for e in diagnose_incident(incident)}

    def test_incident_with_no_runs_does_not_report_ingest(self):
        incident = Incident.objects.create(title="Fresh")
        self.assertEqual(self._stages(incident), ["check", "analyze", "notify"])

    def test_incident_with_only_incident_runs_does_not_report_ingest(self):
        incident = Incident.objects.create(title="New model")
        PipelineRun.objects.create(
            trace_id="t",
            run_id="r",
            incident=incident,
            inbound_payload={"downstream_incident_id": incident.pk},
        )
        self.assertEqual(self._stages(incident), ["check", "analyze", "notify"])

    def test_incident_with_a_legacy_run_still_reports_ingest(self):
        incident = Incident.objects.create(title="Legacy")
        legacy = PipelineRun.objects.create(
            trace_id="t",
            run_id="legacy",
            incident=incident,
            inbound_payload={"driver": "grafana", "payload": {"alerts": []}},
        )
        StageExecution.objects.create(
            pipeline_run=legacy, stage="ingest", status="succeeded", output_ref="ref://p/1"
        )
        entries = self._entries(incident)
        self.assertEqual(list(entries), ["ingest", "check", "analyze", "notify"])
        self.assertEqual(entries["ingest"]["status"], "ok")
        self.assertEqual(entries["ingest"]["runs"], "succeeded in 1/1 runs")

    def test_a_legacy_run_alongside_incident_runs_still_reports_ingest(self):
        incident = Incident.objects.create(title="Mixed")
        legacy = PipelineRun.objects.create(
            trace_id="t",
            run_id="legacy",
            incident=incident,
            inbound_payload={"driver": "grafana", "payload": {}},
        )
        StageExecution.objects.create(
            pipeline_run=legacy, stage="ingest", status="failed", error_message="bad shape"
        )
        PipelineRun.objects.create(
            trace_id="t",
            run_id="child",
            incident=incident,
            inbound_payload={"downstream_incident_id": incident.pk},
        )
        entries = self._entries(incident)
        self.assertEqual(entries["ingest"]["status"], "failed")
        self.assertEqual(entries["ingest"]["runs"], "succeeded in 0/2 runs")

    def test_the_other_stages_are_unaffected_by_the_ingest_rule(self):
        """Dropping INGEST changes nothing about check/analyze/notify."""
        pipe = PipelineDefinition.objects.create(name="no-intel-2", stages=["check", "notify"])
        incident = Incident.objects.create(title="Routed new", pipeline=pipe)
        run = PipelineRun.objects.create(
            trace_id="t",
            run_id="r",
            incident=incident,
            inbound_payload={"downstream_incident_id": incident.pk},
        )
        StageExecution.objects.create(
            pipeline_run=run, stage="check", status="succeeded", output_ref="ref://c"
        )
        entries = self._entries(incident)
        self.assertNotIn("ingest", entries)
        self.assertEqual(entries["check"]["status"], "ok")
        self.assertEqual(entries["analyze"]["status"], "skipped")
        self.assertEqual(entries["notify"]["status"], "never_ran")
