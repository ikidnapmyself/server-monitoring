"""Read model for one alert's page. Returns dataclasses, never querysets."""

import json
from dataclasses import dataclass
from datetime import datetime

from django.urls import reverse

from apps.alerts.describing import (
    describe_alert,
    describe_history,
    describe_incident,
    describe_reeval,
)
from apps.alerts.models import Alert
from apps.console.queries.hosts import alert_host
from config.admin_badges import (
    ALERT_STATUS_TONES,
    INCIDENT_STATUS_TONES,
    SEVERITY_TONES,
    tone_for,
)
from config.describing import Action, Description, Fact

HISTORY_LIMIT = 50


@dataclass(frozen=True)
class IncidentCard:
    pk: int
    what: Description
    status: str
    status_tone: str
    url: str


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    when: datetime
    what: Description


@dataclass(frozen=True)
class AlertDetail:
    pk: int
    what: Description
    do: tuple[Action, ...]
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    node: str
    source: str
    started_at: datetime
    last_seen: datetime
    ended_at: datetime | None
    incident: IncidentCard | None
    history: tuple[HistoryEntry, ...]
    hidden: int
    reeval: tuple[Fact, ...]
    facts: tuple[Fact, ...]
    raw: str
    url: str


def _incident(alert: Alert) -> IncidentCard | None:
    incident = alert.incident
    if incident is None:
        return None
    return IncidentCard(
        pk=incident.pk,
        what=describe_incident(incident),
        status=incident.status,
        status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
        url=reverse("console:incident", args=[incident.pk]),
    )


def _history(alert: Alert, headline: str) -> tuple[tuple[HistoryEntry, ...], int]:
    total = alert.history.count()
    rows = alert.history.order_by("-created_at", "-pk")[:HISTORY_LIMIT]
    entries = []
    for row in rows:
        details = row.details if isinstance(row.details, dict) else {}
        entries.append(
            HistoryEntry(
                id=f"entry-alert_history-{row.pk}",
                when=row.created_at,
                what=describe_history(
                    row.event,
                    alert_headline=headline,
                    old_status=row.old_status,
                    new_status=row.new_status,
                    severity_from=details.get("severity_from"),
                    severity_to=details.get("severity_to"),
                ),
            )
        )
    return tuple(entries), max(0, total - HISTORY_LIMIT)


def build_alert_detail(pk: int, *, can_reeval: bool = False) -> AlertDetail | None:
    """One alert for its page. ``do`` holds only what the caller may do."""
    alert = Alert.objects.select_related("node", "incident").filter(pk=pk).first()
    if alert is None:
        return None
    what = describe_alert(alert)
    history, hidden = _history(alert, what.headline)
    is_checker = isinstance(alert.labels, dict) and bool(alert.labels.get("checker"))
    do = (
        (Action("Re-evaluate against the node's policy", "reevaluate_alert", {"pk": alert.pk}),)
        if can_reeval and is_checker
        else ()
    )
    facts = tuple(
        Fact(label, value, copy=True)
        for label, value in (("Fingerprint", alert.fingerprint), ("Trace ID", alert.trace_id))
        if value
    )
    raw = json.dumps(
        {
            "labels": alert.labels,
            "annotations": alert.annotations,
            "raw_payload": alert.raw_payload,
        },
        indent=2,
        sort_keys=True,
        default=str,
    )
    return AlertDetail(
        pk=alert.pk,
        what=what,
        do=do,
        severity=alert.severity,
        severity_tone=tone_for(SEVERITY_TONES, alert.severity),
        status=alert.status,
        status_tone=tone_for(ALERT_STATUS_TONES, alert.status),
        node=alert_host(
            alert.node.hostname if alert.node else None,
            alert.node.instance_id if alert.node else None,
            alert.labels,
        ),
        source=alert.source,
        started_at=alert.started_at,
        last_seen=alert.updated_at,
        ended_at=alert.ended_at,
        incident=_incident(alert),
        history=history,
        hidden=hidden,
        reeval=describe_reeval(alert),
        facts=facts,
        raw=raw,
        url=reverse("console:alert", args=[alert.pk]),
    )
