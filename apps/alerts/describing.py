"""Alerts and incidents in words, rendered on read from their stored keys.

A checker alert is described by the hub's own checker class from the metrics it carried,
so the sentence is the hub's. The names inside it (paths, process names, hosts) come from
the node and pass through as sent. Webhook alerts keep the name and text their source gave.
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.utils.timezone import is_aware, localtime

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, Node
from apps.alerts.reevaluation import EDITOR_FIXABLE, format_value
from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus, checker_label
from config.admin_badges import SEVERITY_TONES, tone_for
from config.describing import Action, Description, Fact

if TYPE_CHECKING:
    from apps.alerts.reeval_existing import AlertChange, AlertSkip, ReevalReport

UNNAMED_NODE = "Unnamed node"

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


def alert_link(alert_pk: int) -> Action:
    return Action("Open the alert", intent="view_alert", params={"pk": alert_pk})


def alert_name(alert: Alert) -> str:
    """What an alert is about, without the values it last carried."""
    name = _checker_name(alert)
    if name:
        checker = CHECKER_REGISTRY.get(name)
        return (checker.problem if checker else "") or checker_label(name)
    return alert.name or "Unnamed alert"


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


def node_name(hostname: str) -> str:
    """What a node is called on any page. Its instance ID is an identifier, so never that."""
    return hostname or UNNAMED_NODE


def _lead(alerts: list[Alert]) -> Alert | None:
    pool = [a for a in alerts if a.status == AlertStatus.FIRING] or alerts
    return min(pool, key=lambda a: _RANK.get(a.severity, 3)) if pool else None


def _describe(incident: Incident, alerts: list[Alert], node: Node | None) -> Description:
    host = node_name(node.hostname) if node is not None else incident.instance
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


UNREADABLE = "Could not read the re-evaluation record."

RECORD_LABELS = [
    ("severity_reevaluated", "At ingest"),
    ("reevaluated_on_config_change", "On a policy change"),
]


def _record(raw) -> dict | None:
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _severity_clause(record: dict) -> str | None:
    old, new = record.get("from"), record.get("to")
    if not old or not new:
        return None
    return f"{str(old).upper()} to {str(new).upper()}"


def _status_clause(record: dict) -> str | None:
    old, new = record.get("status_from"), record.get("status_to")
    if not old or not new or old == new:
        return None
    return f"status {old} to {new}"


def _when_clause(record: dict) -> str | None:
    at = record.get("at")
    if not isinstance(at, str):
        return None
    try:
        moment = parse_datetime(at)
    except ValueError:
        return None
    if moment is None:
        return None
    if is_aware(moment):
        moment = localtime(moment)
    return date_format(moment, "j M Y")


def _policy_clause(record: dict) -> str | None:
    """How the policy that produced this score reads.

    ``thresholds`` is the raw ``Node.config`` entry for the checker, so its shape
    depends on the checker: a numeric one carries warning/critical, listening_ports
    carries an allowlist of ports instead.
    """
    thresholds = record.get("thresholds")
    if not isinstance(thresholds, dict):
        return None
    parts = []
    if thresholds.get("warning_threshold") is not None:
        parts.append(f"warning {format_value(thresholds['warning_threshold'])}")
    if thresholds.get("critical_threshold") is not None:
        parts.append(f"critical {format_value(thresholds['critical_threshold'])}")
    if parts:
        return "policy " + " / ".join(parts)
    allowlist = thresholds.get("allowlist")
    if not isinstance(allowlist, list):
        return None
    if not allowlist:
        return "policy allows no ports"
    return "policy allows ports " + ", ".join(format_value(port) for port in allowlist)


def describe_record(record: dict) -> str:
    """One sentence for one record, saying only what the record actually carries."""
    parts = []
    severity = _severity_clause(record)
    when = _when_clause(record)
    if severity and when:
        parts.append(f"{severity} on {when}")
    elif severity:
        parts.append(severity)
    elif when:
        parts.append(f"on {when}")
    status = _status_clause(record)
    if status:
        parts.append(status)
    if record.get("value") is not None:
        parts.append(f"value {format_value(record['value'])}")
    policy = _policy_clause(record)
    if policy:
        parts.append(policy)
    if not parts:
        return UNREADABLE
    return ", ".join(parts) + "."


REOPEN_WARNING = (
    "This alert is resolved, but its stored value still breaches the policy. "
    "Applying reopens it and its incident."
)


def describe_reeval(alert: Alert) -> tuple[Fact, ...]:
    """One fact per re-evaluation record the alert carries. Empty when it has none.

    Two writers stamp a record onto ``Alert.annotations``, both as a JSON string: ingest
    writes ``severity_reevaluated`` and ``apply_reeval`` writes
    ``reevaluated_on_config_change``. Both can be present at once, on an alert re-scored at
    ingest and then by an operator, so each fact names the path that wrote it. Nothing here
    raises, because the alert page must open even when a node sent something unreadable.
    """
    annotations = _dict(alert.annotations)
    facts = []
    for key, label in RECORD_LABELS:
        if key in annotations:
            record = _record(annotations[key])
            facts.append(Fact(label, UNREADABLE if record is None else describe_record(record)))
    return tuple(facts)


def _describe_change(change: "AlertChange", named: bool = False) -> Description:
    if change.new_status == AlertStatus.RESOLVED:
        headline, tone = "Would resolve this alert", "ok"
    elif change.reopens:
        headline = f"Would reopen this alert at {change.new_severity}"
        tone = tone_for(SEVERITY_TONES, change.new_severity)
    else:
        headline = f"Would move this alert from {change.old_severity} to {change.new_severity}"
        tone = tone_for(SEVERITY_TONES, change.new_severity)
    detail = f"Its stored value is {change.value_display}."
    if not change.notify:
        detail += " Its incident is acknowledged, so nobody is notified."
    if named:
        headline = f"{alert_name(change.alert)}: {headline[0].lower()}{headline[1:]}"
    return Description(
        headline=headline,
        tone=tone,
        detail=detail,
        action=alert_link(change.alert.pk) if named else None,
    )


def _describe_skip(skip: "AlertSkip", node: Node | None) -> Description:
    action = None
    if node is not None and skip.reason in EDITOR_FIXABLE:
        params = {"pk": node.pk, "checker": _checker_name(skip.alert)}
        action = Action("Edit the node's policy", intent="edit_node_policy", params=params)
    return Description(headline=skip.sentence, tone="info", action=action)


def describe_reeval_report(
    report: "ReevalReport", *, named: bool = False
) -> tuple[Description, ...]:
    """One description per alert the preview looked at. ``named`` when it looked at many."""
    return (
        *(_describe_change(change, named) for change in report.changes),
        *(_describe_skip(skip, report.node) for skip in report.skips),
    )


def describe_reeval_consequences(report: "ReevalReport") -> tuple[str, ...]:
    """What an apply does beyond its rows, said before anything is written."""
    if not report.changes:
        return ()
    runs = report.run_count
    lines = [
        "Every value above is one the node pushed, not a fresh reading.",
        (
            f"Applying queues {runs} pipeline run{'s' if runs != 1 else ''}. "
            "They notify once the inbox drains, if their lane has a channel."
            if runs
            else "Applying queues no pipeline run: no change above belongs to an incident "
            "anyone is notified about."
        ),
    ]
    if report.resolved_count:
        lines.append(
            "Resolving anything also sweeps this node for incidents whose alerts have all "
            "cleared. Each one it resolves gets its own run."
        )
    return tuple(lines)


def describe_reeval_applied(report: "ReevalReport") -> str:
    runs = report.run_count
    return (
        f"Resolved {report.resolved_count}, changed severity on "
        f"{report.severity_changed_count}. Queued {runs} pipeline run{'s' if runs != 1 else ''}."
    )
