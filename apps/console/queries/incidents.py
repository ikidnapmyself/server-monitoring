"""Read model for the incident list. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from apps.alerts.describing import describe_incidents
from apps.alerts.models import Alert, AlertSeverity, Incident, IncidentStatus
from apps.console.filters import (
    STATUS_CHOICES,
    AlertFilter,
    InboxFilter,
    IncidentFilter,
    RunFilter,
)
from apps.console.queries.hosts import host_summary, incident_hosts, node_choices, on_node
from apps.console.queries.overview import SEVERITY_RANK
from config.admin_badges import INCIDENT_STATUS_TONES, SEVERITY_TONES, tone_for

PAGE_SIZE = 50

Choice = tuple[str, str, str | int | None]

STATUS_CHIPS: tuple[Choice, ...] = (
    ("active", "Active", "active"),
    ("open", "Open", "open"),
    ("acknowledged", "Acknowledged", "acknowledged"),
    ("resolved", "Resolved", "resolved"),
    ("closed", "Closed", "closed"),
    ("all", "All", "all"),
)
SEVERITY_CHIPS: tuple[Choice, ...] = (
    ("any", "Any severity", ""),
    *((str(value), str(label), str(value)) for value, label in AlertSeverity.choices),
)
SINCE_CHIPS: tuple[Choice, ...] = (
    ("any", "Any time", ""),
    ("24h", "24h", "24h"),
    ("7d", "7d", "7d"),
    ("30d", "30d", "30d"),
)


@dataclass(frozen=True)
class IncidentListRow:
    pk: int
    title: str
    detail: str
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    nodes: str
    created_at: datetime
    alert_count: int
    url: str


@dataclass(frozen=True)
class Chip:
    key: str
    label: str
    url: str
    active: bool
    count: int | None = None


@dataclass(frozen=True)
class ChipGroup:
    name: str
    label: str
    chips: tuple[Chip, ...]


@dataclass(frozen=True)
class IncidentList:
    rows: tuple[IncidentListRow, ...]
    total: int
    page: int
    pages: int
    prev_url: str
    next_url: str
    groups: tuple[ChipGroup, ...]
    notice: str
    empty: str
    url: str


def chip_group(
    name: str,
    label: str,
    choices: tuple[Choice, ...],
    criteria: IncidentFilter | AlertFilter | RunFilter | InboxFilter,
    base: str,
    counts: dict[str, int] | None = None,
) -> ChipGroup:
    current = getattr(criteria, name)
    return ChipGroup(
        name=name,
        label=label,
        chips=tuple(
            Chip(
                key=key,
                label=text,
                url=base + criteria.querystring(**{name: value}),
                active=value == current,
                count=None if counts is None else counts[key],
            )
            for key, text, value in choices
        ),
    )


def _matching(criteria: IncidentFilter, node_keys: tuple[str, ...] = ()):
    incidents = Incident.objects.all()
    statuses = criteria.statuses()
    if statuses:
        incidents = incidents.filter(status__in=statuses)
    if criteria.severity:
        incidents = incidents.filter(severity=criteria.severity)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        incidents = incidents.filter(created_at__gte=cutoff)
    if criteria.node is not None:
        # A subquery, not a join: a join would cut the alert count down to this node's alerts.
        incidents = incidents.filter(
            Exists(
                Alert.objects.filter(incident=OuterRef("pk")).filter(
                    on_node(criteria.node, node_keys)
                )
            )
        )
    return incidents


def _status_counts(criteria: IncidentFilter, node_keys: tuple[str, ...]) -> dict[str, int]:
    """Incidents per status chip under every filter but the status itself. One query."""
    found = _matching(replace(criteria, status="all"), node_keys).aggregate(
        **{status: Count("pk", filter=Q(status=status)) for status in IncidentStatus.values}
    )
    return {
        key: sum(found.values()) if not statuses else sum(found[s] for s in statuses)
        for key, statuses in STATUS_CHOICES.items()
    }


def _rows(incidents, page: int) -> tuple[IncidentListRow, ...]:
    start = (page - 1) * PAGE_SIZE
    found = list(
        incidents.annotate(rank=SEVERITY_RANK, alerts_total=Count("alerts")).order_by(
            "rank", "-created_at", "-pk"
        )[start : start + PAGE_SIZE]
    )
    hosts = incident_hosts(incident.pk for incident in found)
    described = describe_incidents(found)
    return tuple(
        IncidentListRow(
            pk=incident.pk,
            title=described[incident.pk].headline,
            detail=described[incident.pk].detail,
            severity=incident.severity,
            severity_tone=tone_for(SEVERITY_TONES, incident.severity),
            status=incident.status,
            status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
            nodes=host_summary(hosts.get(incident.pk, ())),
            created_at=incident.created_at,
            alert_count=incident.alerts_total,
            url=reverse("console:incident", args=[incident.pk]),
        )
        for incident in found
    )


def build_incident_list(criteria: IncidentFilter) -> IncidentList:
    base = reverse("console:incidents")
    nodes = node_choices()
    node_keys = next((keys for pk, _, keys in nodes if pk == criteria.node), None)
    if criteria.node is not None and node_keys is None:
        criteria = criteria.with_node_dropped()
    node_keys = node_keys or ()
    node_chips: tuple[Choice, ...] = tuple((str(pk), name, pk) for pk, name, _ in nodes)

    incidents = _matching(criteria, node_keys)
    total = incidents.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        rows = _rows(incidents, page)
        empty = ""
    elif not Incident.objects.exists():
        rows, empty = (), "none"
    elif replace(criteria, dropped=()) == IncidentFilter():
        rows, empty = (), "none_active"
    else:
        rows, empty = (), "no_match"

    groups = [
        chip_group(
            "status", "Status", STATUS_CHIPS, criteria, base, _status_counts(criteria, node_keys)
        ),
        chip_group("severity", "Severity", SEVERITY_CHIPS, criteria, base),
    ]
    if node_chips:
        any_node: Choice = ("any", "Any node", None)
        groups.append(chip_group("node", "Node", (any_node, *node_chips), criteria, base))
    groups.append(chip_group("since", "Since", SINCE_CHIPS, criteria, base))

    return IncidentList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + criteria.querystring(page=page - 1) if page > 1 else "",
        next_url=base + criteria.querystring(page=page + 1) if page < pages else "",
        groups=tuple(groups),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
