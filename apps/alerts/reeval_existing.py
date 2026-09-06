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

from apps.alerts.models import Alert, AlertHistory, Incident, IncidentStatus, Node
from apps.alerts.reevaluation import (
    SCORERS,
    Outcome,
    Skip,
    SkipReason,
    Verdict,
    describe_skip,
    parse_metrics,
    unchanged_skip,
)
from apps.alerts.services import resolve_node

logger = logging.getLogger(__name__)


@dataclass
class AlertChange:
    alert: Alert
    old_severity: str
    old_status: str
    new_severity: str
    new_status: str
    value: float


@dataclass
class AlertSkip:
    alert: Alert
    reason: SkipReason
    sentence: str


@dataclass
class ReevalReport:
    node: Node | None
    changes: list[AlertChange] = field(default_factory=list)
    skips: list[AlertSkip] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status == "resolved")

    @property
    def severity_changed_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status != "resolved")


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


def _outcome_for(alert: Alert, config: dict) -> Outcome:
    """Score one alert, or say why it cannot be scored."""
    checker = (alert.labels or {}).get("checker", "")
    scorer = SCORERS.get(checker)
    if scorer is None:
        return Skip(SkipReason.NO_SCORER, checker=checker)
    metrics = parse_metrics(alert.annotations)
    if metrics is None:
        return Skip(SkipReason.NO_METRICS, checker=checker)
    return scorer(checker, metrics, (config or {}).get(checker))


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
            if outcome.severity != alert.severity or outcome.status != alert.status:
                report.changes.append(
                    AlertChange(
                        alert=alert,
                        old_severity=alert.severity,
                        old_status=alert.status,
                        new_severity=outcome.severity,
                        new_status=outcome.status,
                        value=outcome.value,
                    )
                )
                continue
            outcome = unchanged_skip(checker, (config or {}).get(checker) or {}, outcome)
        report.skips.append(
            AlertSkip(
                alert=alert,
                reason=outcome.reason,
                sentence=describe_skip(outcome, checker=checker, instance_id=instance_id),
            )
        )
    return report


def preview_node_alert_reeval(node: Node) -> ReevalReport:
    """Every open alert on ``node``; kept for the Node admin button and the command."""
    return preview_reeval(ReevalScope.for_node(node))


@transaction.atomic
def apply_reeval(scope: ReevalScope) -> ReevalReport:
    """Apply the re-score: update alerts, history, audit, and incidents."""
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
                "thresholds": (node.config or {}).get(checker, {}),
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
            },
        )
    # Only sweep incidents when something actually resolved — a pure severity
    # change (or a no-op run) must not auto-resolve a manually-reopened incident.
    if report.resolved_count:
        _resolve_incidents_for(node)
    if report.changes:
        logger.info(
            "Config-change re-eval on %s: resolved %d, changed severity on %d",
            node.instance_id,
            report.resolved_count,
            report.severity_changed_count,
        )
    return report


def apply_node_alert_reeval(node: Node) -> ReevalReport:
    """Apply across every open alert on ``node``; kept for the admin button and the command."""
    return apply_reeval(ReevalScope.for_node(node))


def _resolve_incidents_for(node: Node) -> None:
    """Resolve open/ack incidents (touching this node) whose alerts all resolved."""
    incidents = Incident.objects.filter(
        status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
        alerts__labels__instance_id=node.instance_id,
    ).distinct()
    for incident in incidents:
        if incident.alerts.exists() and not incident.alerts.filter(status="firing").exists():
            incident.resolve(summary="All alerts resolved by config-change re-evaluation")
