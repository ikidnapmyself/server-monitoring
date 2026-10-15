"""Read model for the Overview page. Returns dataclasses, never querysets."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import TruncHour
from django.urls import reverse
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    Incident,
    IncidentStatus,
    Node,
)
from config.admin_badges import INCIDENT_STATUS_TONES, SEVERITY_TONES, tone_for
from config.dashboard import NODE_RECENT_MINUTES, build_readiness

OPEN_STATUSES = [IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]
INCIDENT_LIMIT = 20
SPARK_HOURS = 24
ATTENTION_TONES = {"warn": "warning", "error": "critical"}

SEVERITY_RANK = Case(
    When(severity=AlertSeverity.CRITICAL, then=Value(0)),
    When(severity=AlertSeverity.WARNING, then=Value(1)),
    default=Value(2),
    output_field=IntegerField(),
)


@dataclass(frozen=True)
class IncidentRow:
    pk: int
    title: str
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    created_at: datetime
    alert_count: int
    url: str


@dataclass(frozen=True)
class NodeTile:
    pk: int
    name: str
    tone: str
    label: str
    firing: int
    last_seen: datetime
    url: str


@dataclass(frozen=True)
class AttentionItem:
    key: str
    label: str
    detail: str
    tone: str
    url: str


@dataclass(frozen=True)
class Overview:
    headline: str
    incidents: tuple[IncidentRow, ...]
    nodes: tuple[NodeTile, ...]
    spark: tuple[tuple[float, float], ...]
    attention: tuple[AttentionItem, ...]
    open_total: int
    more_url: str

    @property
    def open_hidden(self) -> int:
        return self.open_total - len(self.incidents)


def headline(total: int, critical: int) -> str:
    if not total:
        return "No open incidents."
    noun = "incident" if total == 1 else "incidents"
    if not critical:
        return f"{total} open {noun}."
    return f"{total} open {noun}, {critical} critical."


def open_counts() -> tuple[int, int]:
    counts = Incident.objects.filter(status__in=OPEN_STATUSES).aggregate(
        total=Count("id"),
        critical=Count("id", filter=Q(severity=AlertSeverity.CRITICAL)),
    )
    return counts["total"], counts["critical"]


def open_incidents() -> tuple[IncidentRow, ...]:
    incidents = (
        Incident.objects.filter(status__in=OPEN_STATUSES)
        .annotate(rank=SEVERITY_RANK, alerts_total=Count("alerts"))
        .order_by("rank", "created_at")[:INCIDENT_LIMIT]
    )
    return tuple(
        IncidentRow(
            pk=incident.pk,
            title=incident.title,
            severity=incident.severity,
            severity_tone=tone_for(SEVERITY_TONES, incident.severity),
            status=incident.status,
            status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
            created_at=incident.created_at,
            alert_count=incident.alerts_total,
            url=reverse("console:incident", args=[incident.pk]),
        )
        for incident in incidents
    )


def _node_badge(critical: int, warning: int, firing: int, silent: bool) -> tuple[str, str]:
    """Tone and label for a node tile. Firing alerts outrank silence."""
    if firing:
        tone = "critical" if critical else "warning" if warning else "info"
        return tone, f"{firing} firing"
    if silent:
        return "muted", "quiet"
    return "ok", "healthy"


def _firing_by_node() -> tuple[Counter[int], Counter[int], Counter[int]]:
    # Counted from the alert side so only firing rows are read. Joining from Node
    # reads every alert a node ever had, which grows without bound.
    rows = (
        Alert.objects.filter(status=AlertStatus.FIRING, node__isnull=False)
        .values_list("node_id", "severity")
        .annotate(n=Count("id"))
        .order_by()
    )
    firing: Counter[int] = Counter()
    critical: Counter[int] = Counter()
    warning: Counter[int] = Counter()
    for node_id, severity, n in rows:
        firing[node_id] += n
        if severity == AlertSeverity.CRITICAL:
            critical[node_id] += n
        elif severity == AlertSeverity.WARNING:
            warning[node_id] += n
    return firing, critical, warning


def node_tiles() -> tuple[NodeTile, ...]:
    firing, critical, warning = _firing_by_node()
    cutoff = timezone.now() - timedelta(minutes=NODE_RECENT_MINUTES)
    # This instance's own row is never silent: a hub checked by hand is not a quiet agent.
    self_id = local_instance_id()
    tiles = []
    for node in Node.objects.order_by():
        silent = node.last_seen < cutoff and node.instance_id != self_id
        tone, label = _node_badge(critical[node.pk], warning[node.pk], firing[node.pk], silent)
        tiles.append(
            NodeTile(
                pk=node.pk,
                name=node.hostname or node.instance_id,
                tone=tone,
                label=label,
                firing=firing[node.pk],
                last_seen=node.last_seen,
                url=reverse("admin:alerts_node_change", args=[node.pk]),
            )
        )
    return tuple(sorted(tiles, key=lambda tile: tile.name.casefold()))


def incident_spark() -> tuple[tuple[float, float], ...]:
    """Incidents opened per hour, oldest first.

    The window is the current partial hour plus the ``SPARK_HOURS - 1`` full hours before it.
    """
    this_hour = timezone.now().replace(minute=0, second=0, microsecond=0)
    first = this_hour - timedelta(hours=SPARK_HOURS - 1)
    # Buckets are cut in UTC so they match ``first`` whatever TIME_ZONE is set to.
    counts = dict(
        Incident.objects.filter(created_at__gte=first)
        .annotate(hour=TruncHour("created_at", tzinfo=dt_timezone.utc))
        .values_list("hour")
        .annotate(n=Count("id"))
        .order_by()
    )
    return tuple(
        (float(i), float(counts.get(first + timedelta(hours=i), 0))) for i in range(SPARK_HOURS)
    )


def attention() -> tuple[AttentionItem, ...]:
    return tuple(
        AttentionItem(
            key=entry["key"],
            label=entry["label"],
            detail=entry["detail"],
            tone=ATTENTION_TONES[entry["status"]],
            url=entry["url"],
        )
        for entry in build_readiness()
        if entry["status"] in ATTENTION_TONES
    )


def build_overview() -> Overview:
    total, critical = open_counts()
    return Overview(
        headline=headline(total, critical),
        incidents=open_incidents(),
        nodes=node_tiles(),
        spark=incident_spark(),
        attention=attention(),
        open_total=total,
        more_url=reverse("console:incidents"),
    )
