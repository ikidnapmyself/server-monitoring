"""Pipeline routing: resolve the pipeline lane that handles an alert.

First active pipeline (by priority, then id) whose match() passes wins; None means
no route matched, which the orchestrator fails as a non-retryable ``no_route``
rather than defaulting (migration ``0012`` seeds a catch-all lane so that only
happens once an operator has removed or deactivated it). See the Phase A plan:
docs/plans/2026-08-01-pipeline-routing-phase-a.md.
"""

from typing import Any

from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.alerts.models import AlertSeverity, AlertStatus
from apps.orchestration.models import PipelineDefinition, PipelineOrigin

FIELDS: dict[str, str] = {
    "source": "Source",
    "severity": "Severity",
    "status": "Status",
    "instance": "Host",
    "origin": "Started by",
    "label": "Label",
}
OPS: dict[str, str] = {"is": "is", "is-not": "is not", "in": "is one of", "not-in": "is not one of"}
MEMBERSHIP = ("in", "not-in")
CHOICES: dict[str, type[models.TextChoices]] = {
    "severity": AlertSeverity,
    "status": AlertStatus,
    "origin": PipelineOrigin,
}


def split_field(field: str) -> tuple[str, str]:
    """``"label:team"`` -> ``("label", "team")``; ``"source"`` -> ``("source", "")``."""
    kind, _, key = field.partition(":")
    return kind, key


def value_words(kind: str, value: str) -> str:
    """A choice value as its label; anything else as sent."""
    choices = CHOICES.get(kind)
    if choices is not None and value in choices.values:
        return str(choices(value).label)
    return value


def describe_condition(cond: Any) -> str | None:
    """One condition in words, or None when the console's editor can't express it."""
    if not isinstance(cond, dict) or not isinstance(cond.get("field"), str):
        return None
    kind, key = split_field(cond["field"])
    if kind not in FIELDS or (kind == "label") != bool(key):
        return None
    op, value = cond.get("op", "is"), cond.get("value")
    if op not in OPS:
        return None
    if op in MEMBERSHIP:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None
        said = ", ".join(value_words(kind, v) for v in value)
    elif isinstance(value, str):
        said = value_words(kind, value)
    else:
        return None
    subject = f"{FIELDS[kind]} {key}" if key else FIELDS[kind]
    return f"{subject} {OPS[op]} {said}"


def representable(match: Any) -> bool:
    """True if ``match`` is a list whose every condition the console can read."""
    return isinstance(match, list) and all(describe_condition(c) is not None for c in match)


def describe_match(match: Any) -> str:
    """A route's conditions in words."""
    if not match:
        return "Matches everything"
    words = [describe_condition(c) for c in match] if isinstance(match, list) else [None]
    if None in words:
        return "Uses conditions the console can't show"
    return " and ".join(w for w in words if w)


class StaleRoute(Exception):
    """The route changed between the editor's render and its save."""


def save_route(pk: int, fields: dict, *, expected: int) -> None:
    """Write ``fields`` to route ``pk`` iff it is still at version ``expected``. Bumps it."""
    # QuerySet.update skips auto_now, so updated_at is set by hand.
    updated = PipelineDefinition.objects.filter(pk=pk, version=expected).update(
        **fields, version=F("version") + 1, updated_at=timezone.now()
    )
    if not updated:
        raise StaleRoute


def fallthrough(lane: PipelineDefinition) -> str:
    """Where traffic this route matched goes once it's deleted."""
    if not lane.is_active:
        return "This route is off, so deleting it changes no routing."
    later = (
        PipelineDefinition.objects.filter(is_active=True)
        .filter(Q(priority__gt=lane.priority) | Q(priority=lane.priority, id__gt=lane.id))
        .order_by("priority", "id")
    )
    for other in later:
        if not other.match:
            return (
                f"Traffic it matched goes to the next route that matches, at worst {other.name}, "
                "which matches everything."
            )
    return "Traffic only it matched will match no route, and those runs will fail as unrouted."


def subject_alert(alerts: Any) -> Any:
    """Most severe alert in a batch, ties broken by name then fingerprint.

    One rule for "which alert represents this batch", shared by ingest selection
    and the legacy-snapshot resume path. Returns None for an empty batch.

    The fingerprint key makes the order total: one grouped alertmanager
    notification can carry the same alertname at the same severity for two
    instances, and those belong to different (subject, instance) incidents — an
    arbitrary pick would swing incident_id and title.
    """
    from apps.alerts.services import severity_rank

    return min(
        alerts,
        key=lambda a: (-severity_rank(a.severity), a.name, a.fingerprint),
        default=None,
    )


def material_incident_ids(alerts: Any) -> list[int]:
    """Incident ids of a batch's material alerts, deduplicated, first-seen order.

    One unit of work per incident, not per alert: two alerts of the same incident
    are one situation and must not each start a downstream run. Alerts with no
    incident are dropped — there is nothing for a downstream run to take as its
    subject. Lives here beside ``subject_alert`` so both entry stages share one
    definition rather than keeping two copies in sync.
    """
    seen: set[int] = set()
    ids: list[int] = []
    for alert in alerts:
        incident_id = alert.incident_id
        if incident_id and incident_id not in seen:
            seen.add(incident_id)
            ids.append(incident_id)
    return ids


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
        # firing / resolved. A lane matches on it to route an all-clear away from
        # ANALYZE: there is nothing left to diagnose once a thing has recovered.
        "status": alert.status or "",
        "instance": instance_key_from_labels(labels),
        "labels": labels,
        "origin": origin,
    }


def resolve_pipeline(facts: dict):
    """Return the first active, highest-priority pipeline that matches, else None."""
    for pipeline in PipelineDefinition.objects.filter(is_active=True).order_by("priority", "id"):
        if pipeline.matches(facts):
            return pipeline
    return None
