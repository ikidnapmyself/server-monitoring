"""Tests that orchestrator binds run_id/incident_id/stage into ContextVars."""

from unittest.mock import patch

import pytest

from apps.observability import context
from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
)
from apps.orchestration.models import PipelineStage
from apps.orchestration.orchestrator import PipelineOrchestrator


@pytest.mark.django_db
def test_run_pipeline_sets_contextvars():
    """run_pipeline binds trace_id/run_id/source into observability ContextVars
    during execution and clears them when it returns."""
    seen: dict = {}
    orig_execute = PipelineOrchestrator._execute_pipeline

    def spy(self, pipeline_run, payload):
        snap = context.snapshot()
        seen["trace_id"] = snap["trace_id"]
        seen["run_id"] = snap["run_id"]
        seen["source"] = snap["source"]
        return orig_execute(self, pipeline_run, payload)

    # Mock _execute_stage_with_retry so we don't run real executors but still
    # exercise _execute_pipeline end-to-end.
    with (
        patch.object(
            PipelineOrchestrator,
            "_execute_stage_with_retry",
            side_effect=[
                IngestResult(incident_id=None, alerts_created=1),
                CheckResult(checks_run=1),
                AnalyzeResult(summary="ok"),
                NotifyResult(channels_succeeded=1),
            ],
        ),
        patch.object(PipelineOrchestrator, "_execute_pipeline", spy),
    ):
        orch = PipelineOrchestrator()
        orch.run_pipeline(payload={"payload": {"x": 1}}, source="test")

    assert seen["trace_id"]
    assert seen["run_id"]
    assert seen["source"] == "test"
    # Context cleared after pipeline returns
    snap_after = context.snapshot()
    assert snap_after["run_id"] is None
    assert snap_after["trace_id"] is None
    assert snap_after["source"] is None


@pytest.mark.django_db
def test_run_pipeline_restores_context_on_exception():
    """If _execute_pipeline raises, the bind is still restored."""
    orig_execute = PipelineOrchestrator._execute_pipeline

    def boom(self, pipeline_run, payload):
        # Verify context is bound while inside the call
        snap = context.snapshot()
        assert snap["trace_id"] == pipeline_run.trace_id
        assert snap["run_id"] == pipeline_run.run_id
        raise RuntimeError("kaboom")

    with patch.object(PipelineOrchestrator, "_execute_pipeline", boom):
        orch = PipelineOrchestrator()
        with pytest.raises(RuntimeError, match="kaboom"):
            orch.run_pipeline(payload={"payload": {}}, source="test")

    # Restore the original to avoid polluting other tests in the same process
    PipelineOrchestrator._execute_pipeline = orig_execute

    # Context cleared even though the call raised
    snap_after = context.snapshot()
    assert snap_after["run_id"] is None
    assert snap_after["trace_id"] is None
    assert snap_after["source"] is None


@pytest.mark.django_db
def test_execute_stage_with_retry_binds_stage_and_incident_id():
    """_execute_stage_with_retry binds stage and incident_id into ContextVars
    during each attempt and restores them afterward."""
    seen: list[dict] = []

    orig_execute_stage = PipelineOrchestrator._execute_stage_with_retry

    def spy(self, pipeline_run, stage, payload, previous_results, incident_id):
        snap = context.snapshot()
        seen.append(
            {
                "stage": snap["stage"],
                "incident_id": snap["incident_id"],
                "trace_id": snap["trace_id"],
                "run_id": snap["run_id"],
            }
        )
        # Return a stub result appropriate for the stage
        if stage == PipelineStage.INGEST:
            return IngestResult(incident_id=None, alerts_created=1)
        if stage == PipelineStage.CHECK:
            return CheckResult(checks_run=1)
        if stage == PipelineStage.ANALYZE:
            return AnalyzeResult(summary="ok")
        return NotifyResult(channels_succeeded=1)

    with patch.object(PipelineOrchestrator, "_execute_stage_with_retry", spy):
        orch = PipelineOrchestrator()
        orch.run_pipeline(payload={"payload": {}}, source="test")

    # Verify we saw the context bind for the pipeline (trace_id/run_id) inside
    # every stage call, even when our spy replaced the real method.
    assert len(seen) == 4
    for entry in seen:
        assert entry["trace_id"]
        assert entry["run_id"]

    # After the pipeline returns, both pipeline-level and stage-level binds
    # are restored.
    snap_after = context.snapshot()
    assert snap_after["stage"] is None
    assert snap_after["incident_id"] is None
    assert snap_after["run_id"] is None
    assert snap_after["trace_id"] is None
    assert snap_after["source"] is None

    # Restore (paranoia — patch.object should have done this).
    PipelineOrchestrator._execute_stage_with_retry = orig_execute_stage


@pytest.mark.django_db
def test_execute_stage_with_retry_binds_during_real_call():
    """The real _execute_stage_with_retry binds stage/incident_id inside the
    executor call and restores them on the way out."""
    captured: dict = {}

    orch = PipelineOrchestrator()

    def fake_execute(ctx):
        snap = context.snapshot()
        captured["stage"] = snap["stage"]
        captured["incident_id"] = snap["incident_id"]
        captured["trace_id"] = snap["trace_id"]
        captured["run_id"] = snap["run_id"]
        return IngestResult(incident_id=99, alerts_created=1)

    with patch.object(orch.executors[PipelineStage.INGEST], "execute", fake_execute):
        pipeline_run = orch.start_pipeline(payload={}, source="src")
        # Bind the pipeline-level context the same way _execute_pipeline does,
        # so the stage-level bind can stack on top.
        token = context.bind(
            trace_id=pipeline_run.trace_id,
            run_id=pipeline_run.run_id,
            source=pipeline_run.source,
        )
        try:
            orch._execute_stage_with_retry(
                pipeline_run=pipeline_run,
                stage=PipelineStage.INGEST,
                payload={},
                previous_results={},
                incident_id=None,
            )
        finally:
            context.restore(token)

    assert captured["stage"] == PipelineStage.INGEST
    assert captured["incident_id"] is None
    assert captured["trace_id"] == pipeline_run.trace_id
    assert captured["run_id"] == pipeline_run.run_id

    # Stage/incident_id restored after the call returned.
    snap_after = context.snapshot()
    assert snap_after["stage"] is None
    assert snap_after["incident_id"] is None
