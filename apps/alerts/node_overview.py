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
