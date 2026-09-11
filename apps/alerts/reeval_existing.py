"""Re-evaluate a node's existing open alerts against its current Node.config.

Operator-triggered (admin action + management command). Re-scores stored alert
metrics with the same scorer as ingest, then (on apply) resolves / adjusts
severity, records history + a distinct audit annotation, and auto-resolves
incidents. See docs/plans/2026-08-08-reeval-existing-alerts-design.md.
"""

import json
import logging
from dataclasses import dataclass, field

from django.db import models, transaction
from django.utils import timezone

from apps.alerts.incident_gate import follow_alert
from apps.alerts.models import Alert, AlertHistory, Incident, IncidentStatus, Node
from apps.alerts.reevaluation import (
    EDITOR_FIXABLE,
    SCORERS,
    Outcome,
    Skip,
    SkipReason,
    Verdict,
    describe_skip,
    format_value,
    parse_metrics,
    unchanged_skip,
)
from apps.alerts.services import (
    AlertOrchestrator,
    ProcessingResult,
    announce_incident_change,
    resolve_node,
)
from config.admin_links import admin_url

logger = logging.getLogger(__name__)


@dataclass
class AlertChange:
    alert: Alert
    old_severity: str
    old_status: str
    new_severity: str
    new_status: str
    value: float
    # What apps.alerts.incident_gate.follow_alert said about this change: an
    # acknowledged incident absorbs a refire, and an absorbed change tells nobody.
    notify: bool = True

    @property
    def value_display(self) -> str:
        return format_value(self.value)

    @property
    def reopens(self) -> bool:
        return self.old_status == "resolved" and self.new_status == "firing"


@dataclass
class AlertSkip:
    alert: Alert
    reason: SkipReason
    sentence: str
    # Where an operator fixes this, or None when the reason is not theirs to fix.
    fix_url: str | None = None


@dataclass
class ReevalReport:
    node: Node | None
    changes: list[AlertChange] = field(default_factory=list)
    skips: list[AlertSkip] = field(default_factory=list)
    # Incidents the apply's sweep resolved. Empty on a preview, which cannot know
    # them: the sweep reaches incidents whose alerts had already cleared.
    swept_incident_ids: list[int] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status == "resolved")

    @property
    def severity_changed_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status != "resolved")

    @property
    def reopened_count(self) -> int:
        return sum(1 for c in self.changes if c.reopens)

    @property
    def run_count(self) -> int:
        """How many pipeline runs an apply enqueued, or a preview's floor.

        Shares ``_announced_incident_ids`` with the apply itself, so on an applied
        report the number equals the runs created. On a preview it is a floor, not
        a promise: an apply that resolves anything also sweeps the node for
        incidents whose alerts have all cleared, and those can be incidents no
        preview of this scope ever looked at.
        """
        return len(_announced_incident_ids(self))


@dataclass(frozen=True)
class ReevalScope:
    """Which alerts one re-evaluation covers, and whose policy scores them.

    Alerts are matched by their ``instance_id`` label rather than the ``node`` FK:
    the FK is stamped only at alert creation (``resolve_node``), so an alert created
    before its node registered is unlinked yet still belongs to the node.

    ``checker`` records what the operator asked for, not what was found: a checker
    scope that matches nothing is otherwise indistinguishable from a node scope on a
    quiet node. Nothing here branches on it.
    """

    node: Node | None
    alerts: models.QuerySet[Alert]
    checker: str | None = None

    @classmethod
    def for_node(cls, node: Node) -> "ReevalScope":
        return cls(node=node, alerts=cls._open(node))

    @classmethod
    def for_checker(cls, node: Node, checker: str) -> "ReevalScope":
        return cls(
            node=node,
            alerts=cls._open(node).filter(labels__checker=checker),
            checker=checker,
        )

    @classmethod
    def for_alert(cls, alert: Alert) -> "ReevalScope":
        return cls(node=resolve_node(alert.labels), alerts=Alert.objects.filter(pk=alert.pk))

    @staticmethod
    def _open(node: Node) -> models.QuerySet[Alert]:
        return Alert.objects.filter(labels__instance_id=node.instance_id, status="firing")


def _policy_for(config, checker: str):
    """This checker's slice of a node's config, or the config itself if it is not a mapping.

    A ``Node.config`` that is not a mapping is handed to the scorer unchanged so its
    own contract answers with ``MALFORMED_POLICY``. Reading a key off it here would
    raise instead, and take the whole policy page down with it.
    """
    if isinstance(config, dict):
        return config.get(checker)
    return config


def _outcome_for(alert: Alert, config) -> Outcome:
    """Score one alert, or say why it will not be re-scored.

    A score matching what the alert already says is a skip, not a verdict, so the
    policy that produced it is read once here and quoted from the same lookup.
    """
    checker = (alert.labels or {}).get("checker", "")
    scorer = SCORERS.get(checker)
    if scorer is None:
        return Skip(SkipReason.NO_SCORER, checker=checker)
    metrics = parse_metrics(alert.annotations)
    if metrics is None:
        return Skip(SkipReason.NO_METRICS, checker=checker)
    cfg = _policy_for(config, checker)
    outcome = scorer(checker, metrics, cfg)
    if isinstance(outcome, Verdict) and (
        outcome.severity == alert.severity and outcome.status == alert.status
    ):
        return unchanged_skip(checker, cfg or {}, outcome)
    return outcome


def preview_reeval(scope: ReevalScope) -> ReevalReport:
    """Report which alerts in ``scope`` would change, and why the rest would not.

    An alert the scope cannot score is a skip carrying its own sentence, never a
    silence: the operator asked a question and is owed an answer for every row.
    """
    report = ReevalReport(node=scope.node)
    config = scope.node.config if scope.node else {}
    for alert in scope.alerts:
        labels = alert.labels or {}
        checker = labels.get("checker", "")
        # An unregistered node has no config to quote, so the label is the only name
        # this alert's node has.
        instance_id = scope.node.instance_id if scope.node else labels.get("instance_id", "")
        outcome = _outcome_for(alert, config)
        if isinstance(outcome, Verdict):
            _, notify = follow_alert(
                alert.incident, alert.severity, outcome.severity, alert.status, outcome.status
            )
            report.changes.append(
                AlertChange(
                    alert=alert,
                    old_severity=alert.severity,
                    old_status=alert.status,
                    new_severity=outcome.severity,
                    new_status=outcome.status,
                    value=outcome.value,
                    notify=notify,
                )
            )
            continue
        report.skips.append(
            AlertSkip(
                alert=alert,
                reason=outcome.reason,
                sentence=describe_skip(outcome, checker=checker, instance_id=instance_id),
                fix_url=(admin_url(scope.node) if outcome.reason in EDITOR_FIXABLE else None),
            )
        )
    return report


def preview_node_alert_reeval(node: Node) -> ReevalReport:
    """Every open alert on ``node``; kept for the Node admin button and the command."""
    return preview_reeval(ReevalScope.for_node(node))


@transaction.atomic
def apply_reeval(scope: ReevalScope) -> ReevalReport:
    """Apply the re-score: update alerts, history, audit, incidents, then announce.

    Every incident the apply changed gets one inbox run, enqueued inside this
    transaction so the runs commit with the writes that justify them. Nothing is
    drained here: no pipeline may execute inside the operator's request.

    A scope with no node has no policy to apply, so it writes nothing and returns
    the preview. ``report.node is None`` is how a caller tells that refusal from a
    scope that simply had nothing to change.

    Each applied change then runs the same incident lifecycle ingest runs, so a
    re-fired alert never ends up sitting under a resolved or closed incident.
    """
    report = preview_reeval(scope)
    node = scope.node
    if node is None:
        return report
    for change in report.changes:
        alert = change.alert
        alert.severity = change.new_severity
        alert.status = change.new_status
        if change.new_status == "resolved":
            alert.ended_at = alert.ended_at or timezone.now()
            event = "resolved"
        else:
            alert.ended_at = None
            event = "reevaluated"
        checker = (alert.labels or {}).get("checker", "")
        alert.annotations = dict(alert.annotations or {})
        alert.annotations["reevaluated_on_config_change"] = json.dumps(
            {
                "from": change.old_severity,
                "to": change.new_severity,
                "status_from": change.old_status,
                "status_to": change.new_status,
                "value": change.value,
                "thresholds": _policy_for(node.config, checker) or {},
                "checker": checker,
                "requested_checker": scope.checker,
                "by": "hub-node-policy:config-change",
                "at": timezone.now().isoformat(),
            }
        )
        alert.save()
        AlertHistory.objects.create(
            alert=alert,
            event=event,
            old_status=change.old_status,
            new_status=change.new_status,
            details={
                "severity_from": change.old_severity,
                "severity_to": change.new_severity,
                # On both events, so a reader filters on one key instead of trusting
                # event names to tell policy apart from an ingest-driven resolve.
                "by": "hub-node-policy:config-change",
            },
        )
        _follow_incident(change)
    # Only sweep incidents when something actually resolved — a pure severity
    # change (or a no-op run) must not auto-resolve a manually-reopened incident.
    if report.resolved_count:
        report.swept_incident_ids = _resolve_incidents_for(node)
    if report.changes:
        logger.info(
            "Config-change re-eval on %s: resolved %d, changed severity on %d",
            node.instance_id,
            report.resolved_count,
            report.severity_changed_count,
        )
    for incident in _announced_incidents(report):
        announce_incident_change(incident)
    return report


def apply_node_alert_reeval(node: Node) -> ReevalReport:
    """Apply across every open alert on ``node``; kept for the admin button and the command."""
    return apply_reeval(ReevalScope.for_node(node))


def _follow_incident(change: AlertChange) -> None:
    """Move the incident behind one applied change, the way ingest does.

    Same lifecycle as ``AlertOrchestrator._process_alert``: a firing alert whose
    incident is missing or terminal joins an existing open sibling rather than
    reopening its own (one situation is one open incident), and only
    ``follow_alert`` decides whether the incident reopens and whether anyone hears
    about it. The answer is written back onto ``change`` so the announce loop and
    the run count read the decision this apply actually took.
    """
    alert = change.alert
    incident = _reload(alert.incident_id)
    if change.new_status == "firing" and (
        incident is None or incident.status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED)
    ):
        orchestrator = AlertOrchestrator()
        sibling = orchestrator._find_open_incident(alert)
        if sibling is not None:
            orchestrator._attach_to_incident(alert, sibling, ProcessingResult())
            incident = sibling
    reopen, change.notify = follow_alert(
        incident, change.old_severity, change.new_severity, change.old_status, change.new_status
    )
    if reopen:
        assert incident is not None  # the gate never reopens a missing incident
        incident.reopen()


def _reload(incident_id: int | None) -> Incident | None:
    """The incident as the database has it now, not as an alert cached it."""
    if not incident_id:
        return None
    return Incident.objects.filter(pk=incident_id).first()


def _announced_incidents(report: ReevalReport) -> list[Incident]:
    """The incidents this apply owes a run, reloaded from the database.

    Reloaded because ``_resolve_incidents_for`` has already run: an incident cached on
    an in-memory alert would still report the status it had before that sweep.
    """
    return [Incident.objects.get(pk=pk) for pk in _announced_incident_ids(report)]


def _announced_incident_ids(report: ReevalReport) -> list[int]:
    """Every incident this report changed: notified changes first, then the sweep."""
    ids = _changed_incident_ids(report)
    seen = set(ids)
    for incident_id in report.swept_incident_ids:
        if incident_id in seen:
            continue
        seen.add(incident_id)
        ids.append(incident_id)
    return ids


def _changed_incident_ids(report: ReevalReport) -> list[int]:
    """The distinct incident ids behind ``report.changes``, in first-seen order.

    A change the gate absorbed (an acknowledged incident swallowing a refire) is
    left out: it earned a history row, not a page.
    """
    seen: set[int] = set()
    ids: list[int] = []
    for change in report.changes:
        incident_id = change.alert.incident_id
        if not incident_id or not change.notify or incident_id in seen:
            continue
        seen.add(incident_id)
        ids.append(incident_id)
    return ids


def _resolve_incidents_for(node: Node) -> list[int]:
    """Resolve open/ack incidents (touching this node) whose alerts all resolved.

    Returns the ids it resolved. They are announced like any other change: a sweep
    can reach an incident whose own alerts cleared before this report, and that
    resolution is news to exactly the same people.
    """
    incidents = Incident.objects.filter(
        status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
        alerts__labels__instance_id=node.instance_id,
    ).distinct()
    resolved: list[int] = []
    for incident in incidents:
        if incident.alerts.exists() and not incident.alerts.filter(status="firing").exists():
            incident.resolve(summary="All alerts resolved by config-change re-evaluation")
            resolved.append(incident.pk)
    return resolved
