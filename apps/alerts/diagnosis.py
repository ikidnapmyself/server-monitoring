"""Per-incident stage diagnosis.

A pure, read-only classifier: for each expected pipeline stage of an incident it
reports whether the stage ran cleanly, ran but produced nothing, failed, stalled,
never ran, or was skipped (and why). Aggregates across the incident's pipeline
runs. No side effects — reads the ORM and returns plain dicts, mirroring
``apps.alerts.timeline``.
"""

from __future__ import annotations

from apps.orchestration.models import PipelineStage, StageStatus

# Canonical stage order (display maps ingest->alerts, check->checkers, etc.).
_STAGE_ORDER = [
    PipelineStage.INGEST,
    PipelineStage.CHECK,
    PipelineStage.ANALYZE,
    PipelineStage.NOTIFY,
]

# stage -> (pipeline flag attr or None if always-expected,
#           PipelineRun output-ref attr or None)
_STAGE_META = {
    PipelineStage.INGEST: (None, None),
    PipelineStage.CHECK: ("run_checkers", "checker_output_ref"),
    PipelineStage.ANALYZE: ("run_intelligence", "intelligence_output_ref"),
    PipelineStage.NOTIFY: ("run_notify", "notify_output_ref"),
}

_IN_PROGRESS = {StageStatus.PENDING, StageStatus.RUNNING, StageStatus.RETRYING}


def _is_expected(incident, stage) -> bool:
    """Is this stage expected to run for the incident's routed pipeline?"""
    flag_attr, _ = _STAGE_META[stage]
    if flag_attr is None:
        return True  # ingest always expected
    if incident.pipeline_id is None:
        return True  # un-routed fallback: full pipeline
    return bool(getattr(incident.pipeline, flag_attr))


def diagnose_incident(incident) -> list[dict]:
    """Return one diagnosis entry per expected pipeline stage for ``incident``.

    Each entry: ``stage`` (str), ``status`` (ok|empty|failed|stalled|skipped|
    never_ran), ``detail`` (str|None), ``runs`` (str|None rollup).
    """
    runs = list(incident.pipeline_runs.order_by("-created_at").prefetch_related("stage_executions"))
    total = len(runs)

    entries: list[dict] = []
    for stage in _STAGE_ORDER:
        entries.append(_diagnose_stage(incident, stage, runs, total))
    return entries


def _diagnose_stage(incident, stage, runs, total) -> dict:
    entry = {"stage": stage.value, "status": "never_ran", "detail": None, "runs": None}

    if not _is_expected(incident, stage):
        flag_attr, _ = _STAGE_META[stage]
        entry["status"] = "skipped"
        entry["detail"] = f"config: {flag_attr} disabled"
        return entry

    # Latest execution for this stage: newest run first, highest attempt within.
    latest = None
    succeeded_runs = 0
    for run in runs:
        execs = [e for e in run.stage_executions.all() if e.stage == stage.value]
        if not execs:
            continue
        if any(e.status == StageStatus.SUCCEEDED for e in execs):
            succeeded_runs += 1
        if latest is None:
            latest = max(execs, key=lambda e: e.attempt)

    if total:
        entry["runs"] = f"succeeded in {succeeded_runs}/{total} runs"

    if latest is None:
        entry["status"] = "never_ran"
        return entry

    _classify_from_execution(entry, latest, stage)
    return entry


def _classify_from_execution(entry, exc, stage) -> None:
    """Fill entry['status'] / ['detail'] from the latest StageExecution."""
    if exc.status == StageStatus.SKIPPED:
        entry["status"] = "skipped"
        reason = exc.error_message.removeprefix("Skipped: ") or "no reason recorded"
        entry["detail"] = reason
    elif exc.status == StageStatus.FAILED:
        entry["status"] = "failed"
        entry["detail"] = (
            f"{exc.error_type or 'error'}: {exc.error_message} "
            f"(retryable={exc.error_retryable})"
        )
    elif exc.status in _IN_PROGRESS:
        entry["status"] = "stalled"
    elif exc.status == StageStatus.SUCCEEDED:
        entry["status"] = "empty" if _is_empty(exc, stage) else "ok"


def _is_empty(exc, stage) -> bool:
    """A succeeded stage with no visible output snapshot or refs."""
    _, run_ref_attr = _STAGE_META[stage]
    run_ref_empty = True
    if run_ref_attr is not None:
        run_ref_empty = getattr(exc.pipeline_run, run_ref_attr) == ""
    return (not exc.output_snapshot) and exc.output_ref == "" and run_ref_empty
