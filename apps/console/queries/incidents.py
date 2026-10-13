"""Read model for the incident list. Returns dataclasses, never querysets."""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Exists, OuterRef
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, Incident, Node
from apps.console.filters import IncidentFilter
from apps.console.queries.overview import SEVERITY_RANK
from config.admin_badges import INCIDENT_STATUS_TONES, SEVERITY_TONES, tone_for

PAGE_SIZE = 50

Choice = tuple[str, str, str | int | None]

STATUS_CHOICES: tuple[Choice, ...] = (
    ("active", "Active", "active"),
    ("open", "Open", "open"),
    ("acknowledged", "Acknowledged", "acknowledged"),
    ("resolved", "Resolved", "resolved"),
    ("closed", "Closed", "closed"),
    ("all", "All", "all"),
)
SEVERITY_CHOICES: tuple[Choice, ...] = (
    ("any", "Any severity", ""),
    *((str(value), str(label), str(value)) for value, label in AlertSeverity.choices),
)
SINCE_CHOICES: tuple[Choice, ...] = (
    ("any", "Any time", ""),
    ("24h", "24h", "24h"),
    ("7d", "7d", "7d"),
    ("30d", "30d", "30d"),
)


@dataclass(frozen=True)
class IncidentListRow:
    pk: int
    title: str
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


def _node_choices() -> tuple[Choice, ...]:
    nodes = [
        (pk, hostname or instance_id)
        for pk, hostname, instance_id in Node.objects.order_by().values_list(
            "pk", "hostname", "instance_id"
        )
    ]
    nodes.sort(key=lambda node: node[1].casefold())
    return tuple((str(pk), name, pk) for pk, name in nodes)


def _group(
    name: str, label: str, choices: tuple[Choice, ...], filter: IncidentFilter, base: str
) -> ChipGroup:
    current = getattr(filter, name)
    return ChipGroup(
        name=name,
        label=label,
        chips=tuple(
            Chip(
                key=key,
                label=text,
                url=base + filter.querystring(**{name: value}),
                active=value == current,
            )
            for key, text, value in choices
        ),
    )


def _matching(filter: IncidentFilter):
    incidents = Incident.objects.all()
    statuses = filter.statuses()
    if statuses:
        incidents = incidents.filter(status__in=statuses)
    if filter.severity:
        incidents = incidents.filter(severity=filter.severity)
    cutoff = filter.cutoff(timezone.now())
    if cutoff:
        incidents = incidents.filter(created_at__gte=cutoff)
    if filter.node is not None:
        # A subquery, not a join: a join would cut the alert count down to this node's alerts.
        incidents = incidents.filter(
            Exists(Alert.objects.filter(incident=OuterRef("pk"), node_id=filter.node))
        )
    return incidents


def _node_strings(incident_pks: list[int]) -> dict[int, str]:
    names: defaultdict[int, list[str]] = defaultdict(list)
    seen = (
        Alert.objects.filter(incident_id__in=incident_pks, node__isnull=False)
        .values_list("incident_id", "node__hostname", "node__instance_id")
        .distinct()
        .order_by()
    )
    for incident_pk, hostname, instance_id in seen:
        names[incident_pk].append(hostname or instance_id)
    strings = {}
    for incident_pk, found in names.items():
        found.sort(key=str.casefold)
        strings[incident_pk] = found[0] if len(found) == 1 else f"{found[0]} +{len(found) - 1}"
    return strings


def _rows(incidents, page: int) -> tuple[IncidentListRow, ...]:
    start = (page - 1) * PAGE_SIZE
    found = list(
        incidents.annotate(rank=SEVERITY_RANK, alerts_total=Count("alerts")).order_by(
            "rank", "-created_at", "-pk"
        )[start : start + PAGE_SIZE]
    )
    nodes = _node_strings([incident.pk for incident in found])
    return tuple(
        IncidentListRow(
            pk=incident.pk,
            title=incident.title,
            severity=incident.severity,
            severity_tone=tone_for(SEVERITY_TONES, incident.severity),
            status=incident.status,
            status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
            nodes=nodes.get(incident.pk, ""),
            created_at=incident.created_at,
            alert_count=incident.alerts_total,
            url=reverse("admin:alerts_incident_change", args=[incident.pk]),
        )
        for incident in found
    )


def build_incident_list(filter: IncidentFilter) -> IncidentList:
    base = reverse("console:incidents")
    node_choices = _node_choices()
    if filter.node is not None and all(filter.node != pk for _, _, pk in node_choices):
        filter = filter.with_node_dropped()

    incidents = _matching(filter)
    total = incidents.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(filter.page, pages)
    filter = replace(filter, page=page)

    if total:
        rows = _rows(incidents, page)
        empty = ""
    else:
        rows = ()
        empty = "no_match" if Incident.objects.exists() else "none"

    groups = [
        _group("status", "Status", STATUS_CHOICES, filter, base),
        _group("severity", "Severity", SEVERITY_CHOICES, filter, base),
    ]
    if node_choices:
        any_node: Choice = ("any", "Any node", None)
        groups.append(_group("node", "Node", (any_node, *node_choices), filter, base))
    groups.append(_group("since", "Since", SINCE_CHOICES, filter, base))

    return IncidentList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + filter.querystring(page=page - 1) if page > 1 else "",
        next_url=base + filter.querystring(page=page + 1) if page < pages else "",
        groups=tuple(groups),
        notice=f"Ignored invalid filter: {', '.join(filter.dropped)}." if filter.dropped else "",
        empty=empty,
        url=base + filter.querystring(),
    )
