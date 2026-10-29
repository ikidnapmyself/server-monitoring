"""Read model for the inbox. Returns dataclasses, never querysets."""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Q
from django.urls import reverse

from apps.console.filters import InboxFilter
from apps.console.queries.incidents import ChipGroup, Choice, chip_group
from apps.console.queries.runs import origin_label, run_hosts, run_node
from apps.orchestration.describing import describe_run
from apps.orchestration.inbox import stuck
from apps.orchestration.models import PipelineRun, PipelineStatus
from config.describing import Action, Description

INBOX_LIMIT = 100
STATUS_CHIPS: tuple[Choice, ...] = (
    ("both", "Waiting and in progress", ""),
    ("pending", "Waiting", "pending"),
    ("processing", "In progress", "processing"),
    ("stuck", "Stuck", "stuck"),
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


def _row(run: PipelineRun, hosts, stuck_pks: set[int]) -> InboxRow:
    return InboxRow(
        pk=run.pk,
        what=describe_run(run),
        incident_pk=run.incident_id,
        incident_url=reverse("console:incident", args=[run.incident_id]) if run.incident_id else "",
        node=run_node(run, hosts),
        origin=origin_label(run.origin),
        since=run.updated_at,
        stuck=run.pk in stuck_pks,
        drain=(
            Action("Drain now", "drain_run", {"pk": run.pk})
            if run.status == PipelineStatus.PENDING
            else None
        ),
        url=reverse("console:run", args=[run.pk]),
    )


def build_inbox(criteria: InboxFilter) -> Inbox:
    base = reverse("console:inbox")
    pending = Q(status=PipelineStatus.PENDING)
    processing = Q(status=PipelineStatus.PROCESSING)
    is_stuck = stuck()
    shown = {
        "": pending | processing | is_stuck,
        "pending": pending,
        "processing": processing,
        "stuck": is_stuck,
    }
    counts = PipelineRun.objects.aggregate(
        both=Count("pk", filter=shown[""]),
        pending=Count("pk", filter=pending),
        processing=Count("pk", filter=processing),
        stuck=Count("pk", filter=is_stuck),
    )
    runs = PipelineRun.objects.filter(shown[criteria.status])
    total = runs.count()
    found = list(runs.select_related("node").order_by("created_at", "pk")[:INBOX_LIMIT])
    stuck_pks = set(
        PipelineRun.objects.filter(is_stuck, pk__in=[run.pk for run in found]).values_list(
            "pk", flat=True
        )
    )
    hosts = run_hosts(found)

    do: list[Action] = []
    if counts["pending"]:
        do.append(DRAIN_OLDEST)
    if counts["stuck"]:
        do.append(RECLAIM)

    if found:
        empty = ""
    elif counts["both"]:
        empty = "no_match"
    else:
        empty = "drained"

    return Inbox(
        rows=tuple(_row(run, hosts, stuck_pks) for run in found),
        pending=counts["pending"],
        processing=counts["processing"],
        stuck=counts["stuck"],
        hidden=total - len(found),
        groups=(chip_group("status", "Status", STATUS_CHIPS, criteria, base, counts),),
        do=tuple(do),
        kept=(("status", criteria.status),) if criteria.status else (),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
