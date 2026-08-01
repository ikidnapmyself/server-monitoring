"""Pipeline routing: resolve the pipeline that handles an incident.

First active pipeline (by priority, then id) whose match() passes wins; None means
no route matched (the caller falls back / inboxes). See the Phase A plan:
docs/plans/2026-08-01-pipeline-routing-phase-a.md.
"""

from typing import Any


def facts_from_incident(incident: Any) -> dict:
    """Build routing facts (source / severity / instance / labels) from an incident."""
    from apps.alerts.models import Alert

    labels: dict = {}
    source = ""
    for alert in Alert.objects.filter(incident=incident):
        labels.update(alert.labels or {})
        source = source or alert.source
    return {
        "source": source,
        "severity": getattr(incident, "severity", "") or "",
        "instance": labels.get("instance_id", ""),
        "labels": labels,
    }


def resolve_pipeline(facts: dict):
    """Return the first active, highest-priority pipeline that matches, else None."""
    from apps.orchestration.models import PipelineDefinition

    for pipeline in PipelineDefinition.objects.filter(is_active=True).order_by("priority", "id"):
        if pipeline.matches(facts):
            return pipeline
    return None
