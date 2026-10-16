"""Alerts and incidents in words, rendered on read from their stored keys.

A checker alert is described by the hub's own checker class from the metrics it carried,
so the sentence is the hub's. The names inside it (paths, process names, hosts) come from
the node and pass through as sent. Webhook alerts keep the name and text their source gave.
"""

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, Node
from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus, checker_label
from config.admin_badges import SEVERITY_TONES, tone_for
from config.describing import Action, Description, Fact

HISTORY_TONES = {"resolved": "ok", "created": "info", "refired": "info"}
_HISTORY_HEADLINES = {
    "created": "{} started firing",
    "resolved": "{} cleared",
    "refired": "{} fired again",
}
_QUIET_HISTORY_EVENTS = frozenset({"reevaluated", "updated"})

_RANK: dict[str, int] = {
    AlertSeverity.CRITICAL: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.INFO: 2,
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _checker_name(alert: Alert) -> str:
    name = _dict(alert.labels).get("checker")
    return name if isinstance(name, str) else ""


def check_status_of(alert: Alert) -> CheckStatus:
    payload = _dict(alert.raw_payload)
    unknown = CheckStatus.UNKNOWN.value
    if payload.get("check_status") == unknown or payload.get("status") == unknown:
        return CheckStatus.UNKNOWN
    if alert.status == AlertStatus.RESOLVED:
        return CheckStatus.OK
    if alert.severity == AlertSeverity.CRITICAL:
        return CheckStatus.CRITICAL
    return CheckStatus.WARNING


def _runbook(alert: Alert) -> Action | None:
    annotations = _dict(alert.annotations)
    url = annotations.get("runbook_url") or annotations.get("runbook")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return Action("Open the runbook", intent="open_url", params={"url": url})
    return None


def describe_alert(alert: Alert) -> Description:
    name = _checker_name(alert)
    checker = CHECKER_REGISTRY.get(name)
    if checker is not None:
        metrics = _dict(_dict(alert.raw_payload).get("metrics"))
        return checker().describe(check_status_of(alert), metrics, alert.description)
    firing = alert.status == AlertStatus.FIRING
    tone = tone_for(SEVERITY_TONES, alert.severity) if firing else "ok"
    if name:
        text = alert.description.strip()
        first = text.splitlines()[0] if text else ""
        return Description(
            headline=first or checker_label(name),
            tone=tone,
            detail=alert.description,
            facts=(Fact("Described by", "the node"),),
        )
    return Description(
        headline=alert.name or "Unnamed alert",
        tone=tone,
        detail=alert.description,
        action=_runbook(alert),
    )


def _lead(alerts: list[Alert]) -> Alert | None:
    pool = [a for a in alerts if a.status == AlertStatus.FIRING] or alerts
    return min(pool, key=lambda a: _RANK.get(a.severity, 3)) if pool else None


def _describe(incident: Incident, alerts: list[Alert], node: Node | None) -> Description:
    host = (node.hostname or node.instance_id) if node is not None else incident.instance
    tone = "ok" if incident.is_resolved else tone_for(SEVERITY_TONES, incident.severity)
    facts = (Fact("Incident", f"#{incident.pk}", copy=True),) + tuple(
        Fact(label, value)
        for label, value in (("Subject", incident.subject), ("Instance", incident.instance))
        if value
    )
    lead = _lead(alerts)
    if lead is None:
        headline = f"Incident #{incident.pk}"
        return Description(
            headline=f"{headline} on {host}" if host else headline, tone=tone, facts=facts
        )
    finding = describe_alert(lead)
    name = _checker_name(lead)
    checker = CHECKER_REGISTRY.get(name)
    what = checker.problem if checker else (checker_label(name) if name else finding.headline)
    return Description(
        headline=f"{what} on {host}" if host else what,
        tone=tone,
        detail=finding.headline if name else finding.detail,
        action=finding.action,
        facts=facts + (Fact("Fingerprint", lead.fingerprint, copy=True),) + finding.facts,
    )


def describe_incidents(incidents: Iterable[Incident]) -> dict[int, Description]:
    incidents = list(incidents)
    by_incident: defaultdict[int | None, list[Alert]] = defaultdict(list)
    for alert in Alert.objects.filter(incident_id__in=[i.pk for i in incidents]).order_by(
        "-received_at", "-pk"
    ):
        by_incident[alert.incident_id].append(alert)
    nodes = Node.objects.in_bulk({i.node_id for i in incidents if i.node_id})
    return {i.pk: _describe(i, by_incident[i.pk], nodes.get(i.node_id)) for i in incidents}


def describe_incident(incident: Incident) -> Description:
    return describe_incidents([incident])[incident.pk]


def history_tone(event: str, severity_to: object) -> str:
    """Tone from the severity the row itself moved to, never the alert's current one."""
    if isinstance(severity_to, str) and severity_to in SEVERITY_TONES:
        return SEVERITY_TONES[severity_to]
    return tone_for(HISTORY_TONES, event)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _movement(old: str, new: str) -> str:
    if old and new and old != new:
        return f"{old} to {new}"
    return new or old


def describe_history(
    event: str,
    *,
    alert_headline: str,
    old_status: str,
    new_status: str,
    severity_from: object,
    severity_to: object,
) -> Description:
    sev_from, sev_to = _text(severity_from), _text(severity_to)
    if event in _HISTORY_HEADLINES:
        headline = _HISTORY_HEADLINES[event].format(alert_headline)
    elif sev_from and sev_to and sev_from != sev_to:
        headline = f"{alert_headline} moved from {sev_from} to {sev_to}"
    elif event in _QUIET_HISTORY_EVENTS:
        headline = f"{alert_headline} updated"
    else:
        headline = f"{alert_headline} changed"
    facts = tuple(
        Fact(label, value)
        for label, value in (
            ("Status", _movement(_text(old_status), _text(new_status))),
            ("Severity", _movement(sev_from, sev_to)),
        )
        if value
    )
    return Description(headline=headline, tone=history_tone(event, severity_to), facts=facts)
