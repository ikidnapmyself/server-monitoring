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
from apps.orchestration.models import PipelineOrigin, PipelineRun
from config.describing import Action, Description

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
    incident: Action | None
    node: str
    origin: str
    stage: str
    attempts: int
    created_at: datetime
    link: Action


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


def run_hosts(runs: list[PipelineRun]) -> dict[int, tuple[str, ...]]:
    """The hosts each unlinked run's incident names, for ``run_node``."""
    return incident_hosts(run.incident_id for run in runs if not run.node and run.incident_id)


def run_node(run: PipelineRun, hosts: dict[int, tuple[str, ...]]) -> str:
    if run.node:
        return alert_host(run.node.hostname, run.node.instance_id, None)
    return host_summary(hosts.get(run.incident_id, ())) if run.incident_id else ""


def _matching(criteria: RunFilter, node_keys: tuple[str, ...]):
    runs = PipelineRun.objects.filter(criteria.matches())
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
    return _matching(replace(criteria, status="all"), node_keys).aggregate(
        **{
            key: Count("pk", filter=match) if match else Count("pk")
            for key, match in RUN_STATUS_CHOICES.items()
        }
    )


def _row(run: PipelineRun, hosts: dict[int, tuple[str, ...]]) -> RunListRow:
    what = describe_run(run)
    return RunListRow(
        pk=run.pk,
        what=what,
        incident=(
            Action(f"Incident #{run.incident_id}", "view_incident", {"pk": run.incident_id})
            if run.incident_id
            else None
        ),
        node=run_node(run, hosts),
        origin=origin_label(run.origin),
        stage=STAGE_NAMES.get(run.current_stage or "", ""),
        attempts=run.total_attempts,
        created_at=run.created_at,
        link=Action(what.headline, "view_run", {"pk": run.pk}),
    )


def _rows(runs, start: int, stop: int) -> tuple[RunListRow, ...]:
    found = list(runs.select_related("node").order_by("-created_at", "-pk")[start:stop])
    hosts = run_hosts(found)
    return tuple(_row(run, hosts) for run in found)


def recent_runs(node_pk: int, node_keys: tuple[str, ...], limit: int) -> tuple[RunListRow, ...]:
    """The newest runs on one node by the runs list's node rule, without its counts or chips."""
    return _rows(_matching(RunFilter(node=node_pk), node_keys), 0, limit)


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
        start = (page - 1) * PAGE_SIZE
        rows, empty = _rows(runs, start, start + PAGE_SIZE), ""
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
