"""Tests for the merged incident timeline aggregator."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident
from apps.alerts.timeline import build_incident_timeline
from apps.orchestration.models import PipelineRun, StageExecution


class BuildIncidentTimelineTests(TestCase):
    def _make_alert(self, incident, *, started_at):
        return Alert.objects.create(
            fingerprint="fp-1",
            source="grafana",
            name="High CPU",
            incident=incident,
            started_at=started_at,
        )

    def test_empty_incident_returns_empty_list(self):
        incident = Incident.objects.create(title="Empty")
        assert build_incident_timeline(incident) == []

    def test_merges_all_three_sources_chronologically(self):
        base = timezone.now()
        incident = Incident.objects.create(title="Merged")
        alert = self._make_alert(incident, started_at=base)

        # AlertHistory at base + 10s
        hist = AlertHistory.objects.create(
            alert=alert,
            event="created",
            old_status="",
            new_status="firing",
        )
        AlertHistory.objects.filter(pk=hist.pk).update(created_at=base + timedelta(seconds=10))

        # PipelineRun created at base + 5s
        run = PipelineRun.objects.create(
            trace_id="trace-abc",
            run_id="run-xyz",
            incident=incident,
            notify_output_ref="msg-123",
        )
        PipelineRun.objects.filter(pk=run.pk).update(created_at=base + timedelta(seconds=5))

        # Two stages: ingest at base+15s, notify at base+20s
        StageExecution.objects.create(
            pipeline_run=run,
            stage="ingest",
            status="succeeded",
            started_at=base + timedelta(seconds=15),
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage="notify",
            status="failed",
            started_at=base + timedelta(seconds=20),
            error_message="boom",
        )

        timeline = build_incident_timeline(incident)

        # All entries present.
        kinds = [e["kind"] for e in timeline]
        assert "alert_history" in kinds
        assert "stage" in kinds
        assert "pipeline" in kinds

        # Chronologically ascending by `when`.
        whens = [e["when"] for e in timeline]
        assert whens == sorted(whens)

        # Expected order by time: pipeline (5s), history (10s), ingest (15s), notify (20s)
        assert timeline[0]["kind"] == "pipeline"
        assert timeline[1]["kind"] == "alert_history"
        assert timeline[2]["kind"] == "stage"
        assert timeline[3]["kind"] == "stage"

        # trace/run correlation present on stage + pipeline entries.
        pipeline_entry = timeline[0]
        assert pipeline_entry["run_id"] == "run-xyz"
        assert "run-xyz" in pipeline_entry["label"]
        assert "msg-123" in (pipeline_entry["detail"] or "")

        stage_entries = [e for e in timeline if e["kind"] == "stage"]
        assert all(e["run_id"] == "run-xyz" for e in stage_entries)
        assert stage_entries[0]["label"] == "ingest succeeded"
        assert stage_entries[1]["label"] == "notify failed"
        assert "boom" in (stage_entries[1]["detail"] or "")

        # AlertHistory detail carries old->new status.
        hist_entry = timeline[1]
        assert hist_entry["label"] == "created"
        assert "firing" in (hist_entry["detail"] or "")

    def test_stage_with_null_started_at_is_skipped(self):
        incident = Incident.objects.create(title="NullStage")
        run = PipelineRun.objects.create(trace_id="t", run_id="r-null", incident=incident)
        StageExecution.objects.create(
            pipeline_run=run, stage="ingest", status="pending", started_at=None
        )
        StageExecution.objects.create(
            pipeline_run=run,
            stage="notify",
            status="succeeded",
            started_at=timezone.now(),
        )
        timeline = build_incident_timeline(incident)
        stage_entries = [e for e in timeline if e["kind"] == "stage"]
        assert len(stage_entries) == 1
        assert stage_entries[0]["label"] == "notify succeeded"

    def test_is_pure_no_db_writes(self):
        base = timezone.now()
        incident = Incident.objects.create(title="Pure")
        alert = self._make_alert(incident, started_at=base)
        AlertHistory.objects.create(alert=alert, event="created")
        run = PipelineRun.objects.create(trace_id="t", run_id="r-pure", incident=incident)
        StageExecution.objects.create(
            pipeline_run=run, stage="ingest", status="succeeded", started_at=base
        )

        before = (
            AlertHistory.objects.count(),
            PipelineRun.objects.count(),
            StageExecution.objects.count(),
        )
        build_incident_timeline(incident)
        build_incident_timeline(incident)
        after = (
            AlertHistory.objects.count(),
            PipelineRun.objects.count(),
            StageExecution.objects.count(),
        )
        assert before == after
