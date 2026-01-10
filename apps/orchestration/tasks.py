"""Celery tasks for pipeline orchestration.

These tasks wrap the PipelineOrchestrator for async execution via Celery.
They maintain all the same observability and retry semantics.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task


@shared_task(bind=True)
def run_pipeline_task(
    self,
    payload: dict[str, Any],
    source: str = "unknown",
    trace_id: str | None = None,
    environment: str = "production",
) -> dict[str, Any]:
    """
    Celery task to run the pipeline asynchronously.

    Args:
        payload: Raw payload to process.
        source: Source system.
        trace_id: Optional trace ID.
        environment: Environment name.

    Returns:
        PipelineResult as dict.
    """
    from apps.orchestration.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator()
    result = orchestrator.run_pipeline(
        payload=payload,
        source=source,
        trace_id=trace_id,
        environment=environment,
    )
    return result.to_dict()


@shared_task(bind=True)
def resume_pipeline_task(
    self,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Celery task to resume a failed pipeline.

    Args:
        run_id: Pipeline run ID to resume.
        payload: Payload for the pipeline.

    Returns:
        PipelineResult as dict.
    """
    from apps.orchestration.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator()
    result = orchestrator.resume_pipeline(run_id=run_id, payload=payload)
    return result.to_dict()


@shared_task(bind=True)
def start_pipeline_task(
    self,
    payload: dict[str, Any],
    source: str = "unknown",
    trace_id: str | None = None,
    environment: str = "production",
) -> dict[str, Any]:
    """
    Celery task to start a pipeline and return immediately.

    This creates the pipeline run and queues execution.
    Use this for fire-and-forget scenarios.

    Args:
        payload: Raw payload to process.
        source: Source system.
        trace_id: Optional trace ID.
        environment: Environment name.

    Returns:
        Pipeline run info with trace_id and run_id.
    """
    from apps.orchestration.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator()
    pipeline_run = orchestrator.start_pipeline(
        payload=payload,
        source=source,
        trace_id=trace_id,
        environment=environment,
    )

    # Queue the actual execution
    run_pipeline_task.delay(
        payload=payload,
        source=source,
        trace_id=pipeline_run.trace_id,
        environment=environment,
    )

    return {
        "status": "queued",
        "trace_id": pipeline_run.trace_id,
        "run_id": pipeline_run.run_id,
    }
