"""Pipeline routing: resolve the pipeline lane that handles an alert.

First active pipeline (by priority, then id) whose match() passes wins; None means
no route matched, which the orchestrator fails as a non-retryable ``no_route``
rather than defaulting (migration ``0012`` seeds a catch-all lane so that only
happens once an operator has removed or deactivated it). See the Phase A plan:
docs/plans/2026-08-01-pipeline-routing-phase-a.md.
"""

from typing import Any


def subject_alert(alerts: Any) -> Any:
    """Most severe alert in a batch, ties broken by name then fingerprint.

    One rule for "which alert represents this batch", shared by ingest selection
    and the legacy-snapshot resume path. Returns None for an empty batch.

    The fingerprint key makes the order total: one grouped alertmanager
    notification can carry the same alertname at the same severity for two
    instances, and those belong to different (name, instance) incidents — an
    arbitrary pick would swing incident_id and title.
    """
    from apps.alerts.services import severity_rank

    return min(
        alerts,
        key=lambda a: (-severity_rank(a.severity), a.name, a.fingerprint),
        default=None,
    )


def facts_from_alert(alert: Any, origin: str) -> dict:
    """Routing facts for ONE alert.

    Deliberately single-alert: merging an incident's alerts mixed labels from the
    oldest with the source of the newest, so a multi-alert incident routed on a
    mashup of two different alerts.

    ``instance`` uses the same helper that defines incident grouping, so "which
    host" means one thing everywhere. ``origin`` is the pipeline run's entry point
    (see ``PipelineOrigin``), exposed so lanes can match on where traffic entered.
    """
    from apps.alerts.services import instance_key_from_labels

    labels = alert.labels if isinstance(alert.labels, dict) else {}
    return {
        "source": alert.source or "",
        "severity": alert.severity or "",
        "instance": instance_key_from_labels(labels),
        "labels": labels,
        "origin": origin,
    }


def resolve_pipeline(facts: dict):
    """Return the first active, highest-priority pipeline that matches, else None."""
    from apps.orchestration.models import PipelineDefinition

    for pipeline in PipelineDefinition.objects.filter(is_active=True).order_by("priority", "id"):
        if pipeline.matches(facts):
            return pipeline
    return None
