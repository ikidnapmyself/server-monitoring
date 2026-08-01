"""Phase B: stage selection comes from the matched pipeline's flags.

INGEST always runs; the downstream stages (check/analyze/notify) are chosen from
the resolved PipelineDefinition's run_* flags, and the pipeline is stamped on the
incident right after ingest. checks_only/skip_checkers stay as CLI overrides; a
no-match runs today's full order.
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


class StageSelectionFromFlagsTests(TestCase):
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

    def test_run_checkers_false_skips_check_stage(self):
        PipelineDefinition.objects.create(name="no-check", match=[], priority=1, run_checkers=False)
        result = self._run()
        assert PipelineStage.CHECK not in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_run_intelligence_false_skips_analyze_stage(self):
        PipelineDefinition.objects.create(
            name="no-ai", match=[], priority=1, run_intelligence=False
        )
        result = self._run()
        assert PipelineStage.ANALYZE not in result.stages_completed
        assert PipelineStage.CHECK in result.stages_completed
        assert PipelineStage.NOTIFY in result.stages_completed

    def test_run_notify_false_stops_before_notify(self):
        PipelineDefinition.objects.create(name="silent", match=[], priority=1, run_notify=False)
        result = self._run()
        assert PipelineStage.NOTIFY not in result.stages_completed

    def test_all_flags_false_runs_only_ingest(self):
        PipelineDefinition.objects.create(
            name="inbox-lite",
            match=[],
            priority=1,
            run_checkers=False,
            run_intelligence=False,
            run_notify=False,
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
        # Even a run_checkers=True pipeline is overridden by the payload flag.
        PipelineDefinition.objects.create(name="full", match=[], priority=1, run_checkers=True)
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
        p = PipelineDefinition.objects.create(name="ca", match=[], priority=1)
        incident.pipeline = p
        incident.save(update_fields=["pipeline"])
        before = incident.updated_at
        stages = PipelineOrchestrator()._downstream_stages(incident.id, skip_checkers=False)
        incident.refresh_from_db()
        assert incident.pipeline_id == p.id
        assert incident.updated_at == before  # idempotent: no redundant save
        assert stages == [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]
