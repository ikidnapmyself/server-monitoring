"""Re-evaluate a node's existing open alerts against its current Node.config.

Operator-triggered (admin action + management command). Re-scores stored alert
metrics with the same scorer as ingest, then (on apply) resolves / adjusts
severity, records history + a distinct audit annotation, and auto-resolves
incidents. See docs/plans/2026-08-08-reeval-existing-alerts-design.md.
"""

import json
import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, IncidentStatus, Node
from apps.alerts.reevaluation import SCORERS, parse_metrics

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
class ReevalReport:
    node: Node
    changes: list[AlertChange] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status == "resolved")

    @property
    def severity_changed_count(self) -> int:
        return sum(1 for c in self.changes if c.new_status != "resolved")


def _score_alert(alert: Alert, config: dict) -> tuple[str, str, float] | None:
    checker = (alert.labels or {}).get("checker", "")
    scorer = SCORERS.get(checker)
    if scorer is None:
        return None
    cfg = (config or {}).get(checker)
    metrics = parse_metrics(alert.annotations)
    if metrics is None:
        return None
    return scorer(checker, metrics, cfg)


def preview_node_alert_reeval(node: Node) -> ReevalReport:
    """Report which of the node's open alerts would change; no writes.

    Alerts are matched by their ``instance_id`` label, not the ``node`` FK: the FK
    is stamped only at alert creation (``resolve_node``), so an alert created before
    its node registered is unlinked yet still belongs to the node by label.
    """
    report = ReevalReport(node=node)
    open_alerts = Alert.objects.filter(labels__instance_id=node.instance_id, status="firing")
    for alert in open_alerts:
        outcome = _score_alert(alert, node.config)
        if outcome is None:
            continue
        new_sev, new_status, value = outcome
        if new_sev == alert.severity and new_status == alert.status:
            continue
        report.changes.append(
            AlertChange(
                alert=alert,
                old_severity=alert.severity,
                old_status=alert.status,
                new_severity=new_sev,
                new_status=new_status,
                value=value,
            )
        )
    return report


@transaction.atomic
def apply_node_alert_reeval(node: Node) -> ReevalReport:
    """Apply the re-score: update alerts, history, audit, and incidents."""
    report = preview_node_alert_reeval(node)
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


def _resolve_incidents_for(node: Node) -> None:
    """Resolve open/ack incidents (touching this node) whose alerts all resolved."""
    incidents = Incident.objects.filter(
        status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED],
        alerts__labels__instance_id=node.instance_id,
    ).distinct()
    for incident in incidents:
        if incident.alerts.exists() and not incident.alerts.filter(status="firing").exists():
            incident.resolve(summary="All alerts resolved by config-change re-evaluation")
