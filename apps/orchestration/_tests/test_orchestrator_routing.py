"""Phase B: stage selection comes from the matched pipeline's ``stages`` list.

INGEST always runs; the downstream stages (check/analyze/notify) are the ones the
resolved PipelineDefinition lists in ``stages``, in that order, and the pipeline is
stamped on the incident right after ingest. checks_only/skip_checkers stay as CLI
overrides; a no-match runs today's full order.
"""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Incident
from apps.orchestration.dtos import AnalyzeResult, CheckResult, IngestResult, NotifyResult
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
)
from apps.orchestration.orchestrator import PipelineOrchestrator


class StageSelectionFromStagesListTests(TestCase):
    def setUp(self):
        self.incident = Incident.objects.create(title="High CPU", severity="critical")
        Alert.objects.create(
            fingerprint="fp-cluster",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=self.incident,
            labels={"instance_id": "web-03"},
        )

    def _fake_exec(self, pipeline_run, stage, payload, previous_results, incident_id):
        return {
            PipelineStage.INGEST: IngestResult(incident_id=self.incident.id, alerts_created=1),
            PipelineStage.CHECK: CheckResult(checks_run=1),
            PipelineStage.ANALYZE: AnalyzeResult(summary="s"),
            PipelineStage.NOTIFY: NotifyResult(channels_succeeded=1),
        }[stage]

    def _run(self, payload=None):
        with patch.object(
            PipelineOrchestrator, "_execute_stage_with_retry", side_effect=self._fake_exec
        ):
            return PipelineOrchestrator().run_pipeline(
                payload=payload or {"payload": {}}, source="cluster"
            )

    def test_stages_without_check_skips_check_stage(self):
        PipelineDefinition.objects.create(
            name="no-check", match=[], priority=1, stages=["analyze", "notify"]
        )
        result = self._run()
        assert PipelineStage.CHECK not in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_stages_without_analyze_skips_analyze_stage(self):
        PipelineDefinition.objects.create(
            name="no-ai", match=[], priority=1, stages=["check", "notify"]
        )
        result = self._run()
        assert PipelineStage.ANALYZE not in result.stages_completed
        assert PipelineStage.CHECK in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_stages_without_notify_stops_before_notify(self):
        PipelineDefinition.objects.create(
            name="silent", match=[], priority=1, stages=["check", "analyze"]
        )
        result = self._run()
        assert PipelineStage.NOTIFY not in result.stages_completed

    def test_empty_stages_runs_only_ingest(self):
        PipelineDefinition.objects.create(
            name="inbox-lite",
            match=[],
            priority=1,
            stages=[],
        )
        result = self._run()
        assert result.stages_completed == [PipelineStage.INGEST]
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.status == PipelineStatus.INGESTED

    def test_no_matching_pipeline_runs_full_order(self):
        # Only a pipeline that does NOT match this cluster incident exists.
        PipelineDefinition.objects.create(
            name="grafana-only",
            match=[{"field": "source", "op": "is", "value": "grafana"}],
            priority=1,
        )
        result = self._run()
        assert {
            PipelineStage.INGEST,
            PipelineStage.CHECK,
            PipelineStage.ANALYZE,
            PipelineStage.NOTIFY,
        } <= set(result.stages_completed)

    def test_no_pipelines_at_all_runs_full_order(self):
        result = self._run()
        assert len(result.stages_completed) == 4

    def test_incident_stamped_with_matched_pipeline(self):
        p = PipelineDefinition.objects.create(name="catch-all", match=[], priority=1)
        self._run()
        self.incident.refresh_from_db()
        assert self.incident.pipeline_id == p.id

    def test_skip_checkers_payload_override_still_wins(self):
        # Even a lane that lists CHECK is overridden by the payload flag.
        PipelineDefinition.objects.create(
            name="full", match=[], priority=1, stages=["check", "analyze", "notify"]
        )
        result = self._run(payload={"payload": {}, "skip_checkers": True})
        assert PipelineStage.CHECK not in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_checks_only_payload_override_runs_only_check(self):
        PipelineDefinition.objects.create(name="full", match=[], priority=1)
        result = self._run(payload={"payload": {}, "checks_only": True})
        assert result.stages_completed == [PipelineStage.CHECK]
        run = PipelineRun.objects.get(run_id=result.run_id)
        assert run.status == PipelineStatus.CHECKED


class DownstreamStagesHelperTests(TestCase):
    """Direct unit tests for _downstream_stages edge cases."""

    def _incident(self, source="cluster"):
        incident = Incident.objects.create(title="x", severity="critical")
        Alert.objects.create(
            fingerprint=f"fp-{source}",
            source=source,
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
        )
        return incident

    def test_junk_stages_value_degrades_instead_of_failing_the_run(self):
        """A hand-edited lane must not turn every run into an endless retryable failure.

        ``PipelineStage("sparkle")`` raises ValueError, which the orchestrator's generic
        handler would turn into FAILED/retryable=True *after* INGEST already succeeded.
        Normalising on the model degrades to the valid subset instead.
        """
        incident = self._incident()
        PipelineDefinition.objects.create(
            name="junk", match=[], priority=1, stages=["sparkle", "notify"]
        )
        with self.assertLogs("apps.orchestration.orchestrator", level="WARNING") as logs:
            stages = PipelineOrchestrator()._downstream_stages(incident.id, skip_checkers=False)
        assert stages == [PipelineStage.NOTIFY]
        # The warning names the lane so an operator can find the bad row.
        assert "junk" in logs.output[0]

    def test_valid_lane_logs_no_warning(self):
        incident = self._incident()
        PipelineDefinition.objects.create(
            name="clean", match=[], priority=1, stages=["check", "notify"]
        )
        with patch("apps.orchestration.orchestrator.logger") as mock_logger:
            stages = PipelineOrchestrator()._downstream_stages(incident.id, skip_checkers=False)
        assert stages == [PipelineStage.CHECK, PipelineStage.NOTIFY]
        mock_logger.warning.assert_not_called()

    def test_skip_checkers_removes_check_from_a_matched_lane(self):
        incident = self._incident()
        PipelineDefinition.objects.create(
            name="lane-with-check", match=[], priority=1, stages=["check", "notify"]
        )
        stages = PipelineOrchestrator()._downstream_stages(incident.id, skip_checkers=True)
        assert stages == [PipelineStage.NOTIFY]

    def test_skip_checkers_is_a_noop_for_a_lane_without_check(self):
        incident = self._incident()
        PipelineDefinition.objects.create(
            name="lane-no-check", match=[], priority=1, stages=["analyze", "notify"]
        )
        stages = PipelineOrchestrator()._downstream_stages(incident.id, skip_checkers=True)
        assert stages == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]

    def test_matched_lane_stages_arrive_in_listed_order(self):
        """The list an operator saved is the list the orchestrator executes."""
        incident = self._incident()
        PipelineDefinition.objects.create(
            name="lane-full", match=[], priority=1, stages=["check", "analyze", "notify"]
        )
        stages = PipelineOrchestrator()._downstream_stages(incident.id, skip_checkers=False)
        assert stages == [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]

    def test_no_incident_id_returns_full_default(self):
        stages = PipelineOrchestrator()._downstream_stages(None, skip_checkers=False)
        assert stages == [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]

    def test_no_incident_id_with_skip_checkers(self):
        stages = PipelineOrchestrator()._downstream_stages(None, skip_checkers=True)
        assert stages == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]

    def test_missing_incident_returns_default(self):
        stages = PipelineOrchestrator()._downstream_stages(999999, skip_checkers=False)
        assert PipelineStage.CHECK in stages

    def test_already_stamped_pipeline_is_not_re_saved(self):
        incident = self._incident()
        p = PipelineDefinition.objects.create(
            name="ca", match=[], priority=1, stages=["check", "analyze", "notify"]
        )
        incident.pipeline = p
        incident.save(update_fields=["pipeline"])
        before = incident.updated_at
        stages = PipelineOrchestrator()._downstream_stages(incident.id, skip_checkers=False)
        incident.refresh_from_db()
        assert incident.pipeline_id == p.id
        assert incident.updated_at == before  # idempotent: no redundant save
        assert stages == [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]
