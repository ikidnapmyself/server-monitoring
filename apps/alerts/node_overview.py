"""Panels for the Node admin detail page.

Every panel is a plain function returning a plain dataclass, so the whole page
is testable without driving the admin. ``NodeAdmin.render_change_form`` calls
``build_node_overview`` and the change_form template renders the result.

Nothing here writes. Nothing here knows about requests.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.timesince import timesince

from apps.alerts.identity import local_instance_id
from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
from apps.checkers.models import CheckRun
from config.dashboard import NODE_RECENT_MINUTES

SEVERITY_COLORS = {
    AlertSeverity.CRITICAL: "#dc3545",
    AlertSeverity.WARNING: "#ffc107",
    AlertSeverity.INFO: "#17a2b8",
}

# Worst first: the order both the changelist column and the detail header read in.
SEVERITIES_WORST_FIRST = [AlertSeverity.CRITICAL, AlertSeverity.WARNING, AlertSeverity.INFO]

# An acknowledged incident is a live problem someone has picked up. Counting it
# as handled would make a node look healthy the moment an operator touched it.
UNRESOLVED_INCIDENT_STATUSES = [IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]


@dataclass(frozen=True)
class Identity:
    is_local: bool
    role_label: str
    freshness_status: str  # "ok" | "warn"
    freshness_label: str


def build_identity(node) -> Identity:
    """Role and freshness for the header.

    Freshness reuses the dashboard's own window rather than restating a number,
    so a node that reads amber on the dashboard reads amber here.
    """
    is_local = node.instance_id == local_instance_id()
    now = timezone.now()
    cutoff = now - timedelta(minutes=NODE_RECENT_MINUTES)
    status = "ok" if node.last_seen >= cutoff else "warn"
    return Identity(
        is_local=is_local,
        role_label="This hub" if is_local else "Peer",
        freshness_status=status,
        freshness_label=f"{timesince(node.last_seen, now)} ago",
    )


def unresolved_counts(node) -> dict[str, int]:
    """Unresolved incidents per severity for one node.

    Prefers the annotations ``NodeAdmin.get_queryset`` adds, so rendering the
    whole changelist stays one query. Falls back to a per-node aggregate for a
    node that arrived unannotated. ``distinct=True`` throughout: an incident this
    node raised six alerts on is one incident, not six.
    """
    if hasattr(node, "unresolved_total"):
        return {s: getattr(node, f"unresolved_{s}", 0) for s in SEVERITIES_WORST_FIRST}
    rows = (
        Incident.objects.filter(alerts__node=node, status__in=UNRESOLVED_INCIDENT_STATUSES)
        .values("severity")
        .annotate(count=Count("pk", distinct=True))
    )
    counted = {row["severity"]: row["count"] for row in rows}
    return {s: counted.get(s, 0) for s in SEVERITIES_WORST_FIRST}


def render_severity_chips(node):
    """Linked severity chips, worst first. A quiet node reads as a dash.

    Each chip links to the incident changelist already narrowed to this node and
    severity, reusing the ``alerts__node`` filter rather than a parallel view.
    """
    counts = unresolved_counts(node)
    parts = []
    for severity in SEVERITIES_WORST_FIRST:
        count = counts.get(severity, 0)
        if not count:
            continue
        url = "{}?alerts__node__id__exact={}&status__in={}&severity__exact={}".format(
            reverse("admin:alerts_incident_changelist"),
            node.pk,
            ",".join(UNRESOLVED_INCIDENT_STATUSES),
            severity,
        )
        parts.append(
            format_html(
                '<a href="{}" style="background-color: {}; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px; text-decoration: none;">{} {}</a>',
                url,
                SEVERITY_COLORS.get(severity, "#6c757d"),
                count,
                severity.upper(),
            )
        )
    if not parts:
        return "—"
    return format_html_join(" ", "{}", ((part,) for part in parts))


# The metric each numeric checker is judged on. Promoted from a comment in
# admin.py so the state table and the charts read the same field.
CHECKER_PRIMARY_METRIC = {
    "cpu": "cpu_percent",
    "memory": "memory_percent",
    "disk": "worst_percent",
    "disk_inodes": "worst_percent",
    "disk_temp": "hottest_c",
    "cpu_temp": "hottest_c",
    "io_strain": "busiest_util_percent",
}


@dataclass(frozen=True)
class CheckerRow:
    checker: str
    status: str
    value: str
    observed_at: object
    url: str


def _format_metric(raw) -> str:
    """One display string for a metric value, whatever type it arrived as.

    Peer values come off ``Alert.annotations`` as strings; local values come off
    ``CheckRun.metrics`` as numbers. Anything missing or unparseable reads as a
    dash, never as a stack trace on an operator's page.
    """
    if raw is None or raw == "":
        return "—"
    try:
        return f"{float(raw):.1f}"
    except (TypeError, ValueError):
        return str(raw)


def _local_checker_rows(node) -> list[CheckerRow]:
    """Newest CheckRun per checker for the machine this hub runs on."""
    newest: dict[str, CheckRun] = {}
    runs = CheckRun.objects.filter(hostname=node.hostname).order_by("-executed_at")
    for run in runs.iterator():
        newest.setdefault(run.checker_name, run)
    rows: list[CheckerRow] = []
    for name, run in newest.items():
        metric = CHECKER_PRIMARY_METRIC.get(name)
        raw = (run.metrics or {}).get(metric) if metric else None
        rows.append(
            CheckerRow(
                checker=name,
                status=run.status,
                value=_format_metric(raw),
                observed_at=run.executed_at,
                url=reverse("admin:checkers_checkrun_change", args=[run.pk]),
            )
        )
    return sorted(rows, key=lambda r: r.checker)


def _peer_checker_rows(node) -> list[CheckerRow]:
    """A peer has no CheckRun here — its current state is its Alert rows.

    One Alert per checker per node by fingerprint, updated in place on every
    push, so the newest write is the whole history this hub holds.
    """
    rows: list[CheckerRow] = []
    for alert in node.alerts.order_by("-updated_at"):
        checker = (alert.labels or {}).get("checker")
        if not checker:
            continue  # a webhook alert is not a checker result
        if any(r.checker == checker for r in rows):
            continue
        metric = CHECKER_PRIMARY_METRIC.get(checker)
        raw = (alert.annotations or {}).get(metric) if metric else None
        rows.append(
            CheckerRow(
                checker=checker,
                status=alert.severity,
                value=_format_metric(raw),
                observed_at=alert.updated_at,
                url=reverse("admin:alerts_alert_change", args=[alert.pk]),
            )
        )
    return sorted(rows, key=lambda r: r.checker)


def build_checker_rows(node) -> list[CheckerRow]:
    """Current per-checker state, from whichever source this node has."""
    if build_identity(node).is_local:
        return _local_checker_rows(node)
    return _peer_checker_rows(node)
