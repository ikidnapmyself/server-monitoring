"""Read model for the preflight runs list. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.alerts.describing import node_name
from apps.alerts.models import Node
from apps.checkers.models import PreflightRun
from apps.checkers.preflight_describing import describe_preflight_run
from apps.console.filters import PreflightFilter
from apps.console.queries.incidents import SINCE_CHIPS, ChipGroup, Choice, chip_group
from config.describing import Action, Description

PAGE_SIZE = 50

STATUS_CHIPS: tuple[Choice, ...] = (
    ("error", "Errors", "error"),
    ("warn", "Warnings", "warn"),
    ("ok", "Passed", "ok"),
    ("all", "All", ""),
)
STATUS_TONES = {"error": "critical", "warn": "warning", "ok": "ok"}
TRIGGERS = {"cli": "From the command line"}


@dataclass(frozen=True)
class PreflightListRow:
    pk: int
    what: Description
    trigger: str
    node: str
    created_at: datetime
    link: Action


@dataclass(frozen=True)
class PreflightList:
    rows: tuple[PreflightListRow, ...]
    total: int
    page: int
    pages: int
    prev_url: str
    next_url: str
    groups: tuple[ChipGroup, ...]
    notice: str
    empty: str
    url: str


def trigger_label(triggered_by: str) -> str:
    return TRIGGERS.get(triggered_by, triggered_by.replace("_", " ").capitalize())


def node_names(instance_ids) -> dict[str, str]:
    """The name of each node a preflight ran on. Its instance ID is an identifier, so never that."""
    found = Node.objects.filter(instance_id__in=set(instance_ids) - {""})
    return {node.instance_id: node_name(node.hostname) for node in found}


def _matching(criteria: PreflightFilter):
    runs = PreflightRun.objects.all()
    if criteria.status:
        runs = runs.filter(overall_status=criteria.status)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        runs = runs.filter(created_at__gte=cutoff)
    return runs


def _status_counts(criteria: PreflightFilter) -> dict[str, int]:
    return _matching(replace(criteria, status="")).aggregate(
        **{
            key: Count("pk", filter=Q(overall_status=value))
            for key, _, value in STATUS_CHIPS
            if value
        },
        all=Count("pk"),
    )


def _row(run: PreflightRun, names: dict[str, str]) -> PreflightListRow:
    what = describe_preflight_run(run)
    return PreflightListRow(
        pk=run.pk,
        what=what,
        trigger=trigger_label(run.triggered_by),
        node=names.get(run.instance_id, ""),
        created_at=run.created_at,
        link=Action(what.headline, "view_preflight_run", {"pk": run.pk}),
    )


def build_preflight_list(criteria: PreflightFilter) -> PreflightList:
    base = reverse("console:preflight")
    runs = _matching(criteria)
    total = runs.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        start = (page - 1) * PAGE_SIZE
        found = list(runs.order_by("-created_at", "-pk")[start : start + PAGE_SIZE])
        names = node_names(run.instance_id for run in found)
        rows, empty = tuple(_row(run, names) for run in found), ""
    elif not PreflightRun.objects.exists():
        rows, empty = (), "none"
    else:
        rows, empty = (), "no_match"

    groups = (
        chip_group(
            "status", "Status", STATUS_CHIPS, criteria, base, _status_counts(criteria), STATUS_TONES
        ),
        chip_group("since", "Since", SINCE_CHIPS, criteria, base),
    )
    return PreflightList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + criteria.querystring(page=page - 1) if page > 1 else "",
        next_url=base + criteria.querystring(page=page + 1) if page < pages else "",
        groups=groups,
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
