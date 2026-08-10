"""Merged incident timeline aggregator.

A pure read-only function that gathers an incident's three lifecycle sources —
alert history events, pipeline stage executions, and pipeline runs — into a
single chronologically ordered list of plain dicts. No side effects: it only
reads the ORM and returns data, so callers (admin display, APIs) can render it
however they like.
"""

from apps.alerts.models import AlertHistory, Incident


def build_incident_timeline(incident: Incident) -> list[dict]:
    """Return a chronological list of timeline entries for ``incident``.

    Each entry is a dict with keys:

    - ``when``: timezone-aware datetime of the event (sort key).
    - ``kind``: one of ``"alert_history"``, ``"stage"``, ``"pipeline"``.
    - ``label``: short human-readable string.
    - ``detail``: optional string with extra context (may be ``None``).
    - ``run_id``: present on ``stage``/``pipeline`` entries for correlation.

    Entries whose event time is unknown are skipped. Specifically, a
    ``StageExecution`` with a null ``started_at`` (never started) has no place on
    a chronological timeline, so it is omitted.
    """
    entries: list[dict] = []

    # 1) AlertHistory events (hang off Alert, reached via alert__incident).
    history = AlertHistory.objects.filter(alert__incident=incident)
    for h in history:
        detail = None
        if h.old_status or h.new_status:
            detail = f"{h.old_status or '—'} → {h.new_status or '—'}"
        entries.append(
            {
                "when": h.created_at,
                "kind": "alert_history",
                "label": h.event,
                "detail": detail,
            }
        )

    # 2) PipelineRun creation + 3) its StageExecutions.
    for run in incident.pipeline_runs.all():
        entries.append(
            {
                "when": run.created_at,
                "kind": "pipeline",
                "label": f"run {run.run_id} created",
                "detail": run.notify_output_ref or None,
                "run_id": run.run_id,
            }
        )
        for stage in run.stage_executions.all():
            if stage.started_at is None:
                # Never started — no chronological position, skip.
                continue
            entries.append(
                {
                    "when": stage.started_at,
                    "kind": "stage",
                    "label": f"{stage.stage} {stage.status}",
                    "detail": stage.error_message or None,
                    "run_id": run.run_id,
                }
            )

    entries.sort(key=lambda e: e["when"])
    return entries
