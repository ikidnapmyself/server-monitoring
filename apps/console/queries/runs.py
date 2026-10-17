"""Read model for the runs list. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert
from apps.console.filters import RUN_STATUS_CHOICES, RunFilter
from apps.console.queries.hosts import (
    alert_host,
    host_summary,
    incident_hosts,
    node_choices,
    on_node,
)
from apps.console.queries.incidents import SINCE_CHIPS, ChipGroup, Choice, chip_group
from apps.orchestration.describing import STAGE_NAMES, describe_run
from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus
from config.describing import Description

PAGE_SIZE = 50

STATUS_CHIPS: tuple[Choice, ...] = (
    ("failed", "Failed", "failed"),
    ("active", "Queued and in progress", "active"),
    ("finished", "Finished", "finished"),
    ("all", "All", "all"),
)
ORIGIN_CHIPS: tuple[Choice, ...] = (
    ("any", "Any origin", ""),
    *((str(value), str(label), str(value)) for value, label in PipelineOrigin.choices),
)
ORIGIN_LABELS = dict(PipelineOrigin.choices)


@dataclass(frozen=True)
class RunListRow:
    pk: int
    what: Description
    incident_pk: int | None
    incident_url: str
    node: str
    origin: str
    stage: str
    attempts: int
    created_at: datetime
    url: str


@dataclass(frozen=True)
class RunList:
    rows: tuple[RunListRow, ...]
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


def origin_label(origin: str) -> str:
    return str(ORIGIN_LABELS.get(origin, origin.replace("_", " ").capitalize()))


def _matching(criteria: RunFilter, node_keys: tuple[str, ...]):
    runs = PipelineRun.objects.all()
    statuses = criteria.statuses()
    if statuses:
        runs = runs.filter(status__in=statuses)
    if criteria.origin:
        runs = runs.filter(origin=criteria.origin)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        runs = runs.filter(created_at__gte=cutoff)
    if criteria.node is not None:
        # Most producers leave run.node empty, so an unlinked run is about the node its
        # incident's alerts name.
        about = Alert.objects.filter(incident=OuterRef("incident_id")).filter(
            on_node(criteria.node, node_keys)
        )
        runs = runs.filter(Q(node_id=criteria.node) | Q(node__isnull=True) & Exists(about))
    if criteria.q:
        q = criteria.q
        alerts = Alert.objects.filter(incident=OuterRef("incident_id"), fingerprint=q)
        runs = runs.filter(
            Q(run_id__icontains=q)
            | Q(trace_id__icontains=q)
            | Q(alert_fingerprint__icontains=q)
            | Exists(alerts)
        )
    return runs


def _status_counts(criteria: RunFilter, node_keys: tuple[str, ...]) -> dict[str, int]:
    found = _matching(replace(criteria, status="all"), node_keys).aggregate(
        **{status: Count("pk", filter=Q(status=status)) for status in PipelineStatus.values}
    )
    return {
        key: sum(found.values()) if not statuses else sum(found[s] for s in statuses)
        for key, statuses in RUN_STATUS_CHOICES.items()
    }


def _rows(runs, page: int) -> tuple[RunListRow, ...]:
    start = (page - 1) * PAGE_SIZE
    found = list(
        runs.select_related("node").order_by("-created_at", "-pk")[start : start + PAGE_SIZE]
    )
    hosts = incident_hosts(run.incident_id for run in found if not run.node and run.incident_id)
    return tuple(
        RunListRow(
            pk=run.pk,
            what=describe_run(run),
            incident_pk=run.incident_id,
            incident_url=(
                reverse("console:incident", args=[run.incident_id]) if run.incident_id else ""
            ),
            node=(
                alert_host(run.node.hostname, run.node.instance_id, None)
                if run.node
                else host_summary(hosts.get(run.incident_id, ()))
            ),
            origin=origin_label(run.origin),
            stage=STAGE_NAMES.get(run.current_stage or "", ""),
            attempts=run.total_attempts,
            created_at=run.created_at,
            url=reverse("console:run", args=[run.pk]),
        )
        for run in found
    )


def build_run_list(criteria: RunFilter) -> RunList:
    base = reverse("console:runs")
    nodes = node_choices()
    node_keys = next((keys for pk, _, keys in nodes if pk == criteria.node), None)
    if criteria.node is not None and node_keys is None:
        criteria = criteria.without("node")
    node_keys = node_keys or ()

    runs = _matching(criteria, node_keys)
    total = runs.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        rows, empty = _rows(runs, page), ""
    elif not PipelineRun.objects.exists():
        rows, empty = (), "none"
    else:
        rows, empty = (), "no_match"

    counts = _status_counts(criteria, node_keys)
    groups = [
        chip_group("status", "Status", STATUS_CHIPS, criteria, base, counts),
        chip_group("origin", "Origin", ORIGIN_CHIPS, criteria, base),
    ]
    if nodes:
        any_node: Choice = ("any", "Any node", None)
        node_chips = tuple((str(pk), name, pk) for pk, name, _ in nodes)
        groups.append(chip_group("node", "Node", (any_node, *node_chips), criteria, base))
    groups.append(chip_group("since", "Since", SINCE_CHIPS, criteria, base))

    return RunList(
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
