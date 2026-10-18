"""Read model for the inbox. Returns dataclasses, never querysets."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.console.filters import InboxFilter
from apps.console.queries.incidents import ChipGroup, Choice, chip_group
from apps.console.queries.runs import origin_label, run_hosts, run_node
from apps.orchestration.describing import describe_run
from apps.orchestration.inbox import DEFAULT_STALE_MINUTES
from apps.orchestration.models import PipelineRun, PipelineStatus
from config.describing import Action, Description

INBOX_LIMIT = 100
STATUS_CHIPS: tuple[Choice, ...] = (
    ("both", "Waiting and in progress", ""),
    ("pending", "Waiting", "pending"),
    ("processing", "In progress", "processing"),
)
DRAIN_OLDEST = Action("Drain the oldest runs", "drain_inbox")
RECLAIM = Action("Return stuck runs to the queue", "reclaim_inbox")


@dataclass(frozen=True)
class InboxRow:
    pk: int
    what: Description
    incident_pk: int | None
    incident_url: str
    node: str
    origin: str
    since: datetime
    stuck: bool
    drain: Action | None
    url: str


@dataclass(frozen=True)
class Inbox:
    rows: tuple[InboxRow, ...]
    pending: int
    processing: int
    stuck: int
    hidden: int
    groups: tuple[ChipGroup, ...]
    do: tuple[Action, ...]
    kept: tuple[tuple[str, str], ...]
    notice: str
    empty: str
    url: str


def _row(run: PipelineRun, hosts, cutoff: datetime, can_drain: bool) -> InboxRow:
    return InboxRow(
        pk=run.pk,
        what=describe_run(run),
        incident_pk=run.incident_id,
        incident_url=reverse("console:incident", args=[run.incident_id]) if run.incident_id else "",
        node=run_node(run, hosts),
        origin=origin_label(run.origin),
        since=run.updated_at,
        stuck=run.status == PipelineStatus.PROCESSING and run.updated_at < cutoff,
        drain=(
            Action("Drain now", "drain_run", {"pk": run.pk})
            if can_drain and run.status == PipelineStatus.PENDING
            else None
        ),
        url=reverse("console:run", args=[run.pk]),
    )


def build_inbox(criteria: InboxFilter, *, can_drain: bool = False) -> Inbox:
    base = reverse("console:inbox")
    cutoff = timezone.now() - timedelta(minutes=DEFAULT_STALE_MINUTES)
    is_stuck = Q(status=PipelineStatus.PROCESSING, updated_at__lt=cutoff)
    counts = PipelineRun.objects.aggregate(
        pending=Count("pk", filter=Q(status=PipelineStatus.PENDING)),
        processing=Count("pk", filter=Q(status=PipelineStatus.PROCESSING)),
        stuck=Count("pk", filter=is_stuck),
    )
    runs = PipelineRun.objects.filter(status__in=criteria.statuses())
    total = runs.count()
    found = list(runs.select_related("node").order_by("created_at", "pk")[:INBOX_LIMIT])
    hosts = run_hosts(found)

    do: list[Action] = []
    if can_drain and counts["pending"]:
        do.append(DRAIN_OLDEST)
    if can_drain and counts["stuck"]:
        do.append(RECLAIM)

    chip_counts = {
        "both": counts["pending"] + counts["processing"],
        "pending": counts["pending"],
        "processing": counts["processing"],
    }
    if found:
        empty = ""
    elif chip_counts["both"]:
        empty = "no_match"
    else:
        empty = "drained"

    return Inbox(
        rows=tuple(_row(run, hosts, cutoff, can_drain) for run in found),
        pending=counts["pending"],
        processing=counts["processing"],
        stuck=counts["stuck"],
        hidden=total - len(found),
        groups=(chip_group("status", "Status", STATUS_CHIPS, criteria, base, chip_counts),),
        do=tuple(do),
        kept=(("status", criteria.status),) if criteria.status else (),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
