"""Read model for the alert list. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from apps.alerts.describing import describe_alert
from apps.alerts.models import Alert, AlertStatus
from apps.console.filters import ALERT_STATUS_CHOICES, AlertFilter
from apps.console.queries.hosts import alert_host, node_choices, on_node
from apps.console.queries.incidents import (
    SEVERITY_CHIPS,
    SINCE_CHIPS,
    ChipGroup,
    Choice,
    chip_group,
)
from apps.orchestration.models import PipelineRun
from config.admin_badges import ALERT_STATUS_TONES, SEVERITY_TONES, tone_for

PAGE_SIZE = 50

STATUS_CHIPS: tuple[Choice, ...] = (
    ("firing", "Firing", "firing"),
    ("resolved", "Resolved", "resolved"),
    ("all", "All", "all"),
)


@dataclass(frozen=True)
class AlertListRow:
    pk: int
    title: str
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    node: str
    source: str
    last_seen: datetime
    incident_pk: int | None
    incident_url: str
    url: str


@dataclass(frozen=True)
class AlertList:
    rows: tuple[AlertListRow, ...]
    total: int
    page: int
    pages: int
    prev_url: str
    next_url: str
    groups: tuple[ChipGroup, ...]
    q: str
    kept: tuple[tuple[str, str], ...]
    notice: str
    empty: str
    url: str


def _matching(criteria: AlertFilter, node_keys: tuple[str, ...]):
    alerts = Alert.objects.all()
    statuses = criteria.statuses()
    if statuses:
        alerts = alerts.filter(status__in=statuses)
    if criteria.severity:
        alerts = alerts.filter(severity=criteria.severity)
    if criteria.source:
        alerts = alerts.filter(source=criteria.source)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        alerts = alerts.filter(updated_at__gte=cutoff)
    if criteria.node is not None:
        alerts = alerts.filter(on_node(criteria.node, node_keys))
    if criteria.q:
        q = criteria.q
        runs = PipelineRun.objects.filter(incident_id=OuterRef("incident_id"), trace_id=q)
        alerts = alerts.filter(
            Q(name__icontains=q) | Q(fingerprint__icontains=q) | Q(trace_id=q) | Exists(runs)
        )
    return alerts


def _status_counts(criteria: AlertFilter, node_keys: tuple[str, ...]) -> dict[str, int]:
    found = _matching(replace(criteria, status="all"), node_keys).aggregate(
        **{status: Count("pk", filter=Q(status=status)) for status in AlertStatus.values}
    )
    return {
        key: sum(found.values()) if not statuses else sum(found[s] for s in statuses)
        for key, statuses in ALERT_STATUS_CHOICES.items()
    }


def _rows(alerts, page: int) -> tuple[AlertListRow, ...]:
    start = (page - 1) * PAGE_SIZE
    found = alerts.select_related("node").order_by("-updated_at", "-pk")[start : start + PAGE_SIZE]
    return tuple(
        AlertListRow(
            pk=alert.pk,
            title=describe_alert(alert).headline,
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
            last_seen=alert.updated_at,
            incident_pk=alert.incident_id,
            incident_url=(
                reverse("console:incident", args=[alert.incident_id]) if alert.incident_id else ""
            ),
            url=reverse("console:alert", args=[alert.pk]),
        )
        for alert in found
    )


def build_alert_list(criteria: AlertFilter) -> AlertList:
    base = reverse("console:alerts")
    nodes = node_choices()
    node_keys = next((keys for pk, _, keys in nodes if pk == criteria.node), None)
    if criteria.node is not None and node_keys is None:
        criteria = criteria.without("node")
    sources = sorted(
        Alert.objects.order_by().values_list("source", flat=True).distinct(), key=str.casefold
    )
    if criteria.source and criteria.source not in sources:
        criteria = criteria.without("source")
    node_keys = node_keys or ()

    alerts = _matching(criteria, node_keys)
    total = alerts.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        rows, empty = _rows(alerts, page), ""
    elif not Alert.objects.exists():
        rows, empty = (), "none"
    elif replace(criteria, dropped=()) == AlertFilter():
        rows, empty = (), "none_firing"
    else:
        rows, empty = (), "no_match"

    counts = _status_counts(criteria, node_keys)
    groups = [
        chip_group("status", "Status", STATUS_CHIPS, criteria, base, counts),
        chip_group("severity", "Severity", SEVERITY_CHIPS, criteria, base),
    ]
    if nodes:
        any_node: Choice = ("any", "Any node", None)
        node_chips = tuple((str(pk), name, pk) for pk, name, _ in nodes)
        groups.append(chip_group("node", "Node", (any_node, *node_chips), criteria, base))
    if len(sources) > 1:
        any_source: Choice = ("any", "Any source", "")
        source_chips = tuple((s, s, s) for s in sources)
        groups.append(chip_group("source", "Source", (any_source, *source_chips), criteria, base))
    groups.append(chip_group("since", "Since", SINCE_CHIPS, criteria, base))

    return AlertList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + criteria.querystring(page=page - 1) if page > 1 else "",
        next_url=base + criteria.querystring(page=page + 1) if page < pages else "",
        groups=tuple(groups),
        q=criteria.q,
        kept=criteria.kept(),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
