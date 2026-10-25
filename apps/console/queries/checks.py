"""Read model for the check runs list. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.checkers.checkers import CHECKER_REGISTRY, checker_label
from apps.checkers.describing import check_status, describe_check
from apps.checkers.models import CheckRun
from apps.console.filters import CHECK_STATUS_CHOICES, CheckFilter
from apps.console.queries.incidents import SINCE_CHIPS, ChipGroup, Choice, chip_group
from config.admin_badges import CHECK_STATUS_TONES
from config.describing import Action, Description, format_duration

PAGE_SIZE = 50

STATUS_CHIPS: tuple[Choice, ...] = (
    ("failing", "Failing", "failing"),
    ("critical", "Critical", "critical"),
    ("warning", "Warning", "warning"),
    ("unknown", "Unknown", "unknown"),
    ("ok", "OK", "ok"),
    ("skipped", "Skipped", "skipped"),
    ("all", "All", ""),
)
STATUS_TONES = {"failing": "critical", **CHECK_STATUS_TONES}


@dataclass(frozen=True)
class CheckListRow:
    pk: int
    what: Description
    checker: str
    host: str
    executed_at: datetime
    took: str
    link: Action


@dataclass(frozen=True)
class CheckList:
    rows: tuple[CheckListRow, ...]
    total: int
    page: int
    pages: int
    prev_url: str
    next_url: str
    groups: tuple[ChipGroup, ...]
    checkers: tuple[tuple[str, str], ...]
    hosts: tuple[str, ...]
    q: str
    checker: str
    host: str
    kept: tuple[tuple[str, str], ...]
    notice: str
    empty: str
    url: str


def describe_check_run(run: CheckRun) -> Description:
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    return describe_check(run.checker_name, check_status(run.status), metrics, run.message)


def took(duration_ms: float) -> str:
    return format_duration(duration_ms / 1000)


def _matching(criteria: CheckFilter):
    runs = CheckRun.objects.all()
    statuses = criteria.statuses()
    if statuses:
        runs = runs.filter(status__in=statuses)
    if criteria.checker:
        runs = runs.filter(checker_name=criteria.checker)
    if criteria.host:
        runs = runs.filter(hostname=criteria.host)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        runs = runs.filter(executed_at__gte=cutoff)
    if criteria.q:
        runs = runs.filter(Q(message__icontains=criteria.q) | Q(hostname__icontains=criteria.q))
    return runs


def _status_counts(criteria: CheckFilter) -> dict[str, int]:
    return _matching(replace(criteria, status="")).aggregate(
        **{
            key: Count("pk", filter=Q(status__in=CHECK_STATUS_CHOICES[str(value)]))
            for key, _, value in STATUS_CHIPS
            if value
        },
        all=Count("pk"),
    )


def _row(run: CheckRun) -> CheckListRow:
    what = describe_check_run(run)
    return CheckListRow(
        pk=run.pk,
        what=what,
        checker=checker_label(run.checker_name),
        host=run.hostname,
        executed_at=run.executed_at,
        took=took(run.duration_ms),
        link=Action(what.headline, "view_check_run", {"pk": run.pk}),
    )


def checker_choices() -> tuple[tuple[str, str], ...]:
    """Every checker this hub has or a row names, as its key and label, by label."""
    stored = CheckRun.objects.order_by().values_list("checker_name", flat=True).distinct()
    names = {*CHECKER_REGISTRY, *stored} - {""}
    return tuple(sorted(((name, checker_label(name)) for name in names), key=lambda c: c[1]))


def host_choices(current: str) -> tuple[str, ...]:
    hosts = {*CheckRun.objects.order_by().values_list("hostname", flat=True).distinct()} - {""}
    if current:
        hosts.add(current)
    return tuple(sorted(hosts, key=str.casefold))


def build_check_list(criteria: CheckFilter) -> CheckList:
    base = reverse("console:checks")
    runs = _matching(criteria)
    total = runs.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        start = (page - 1) * PAGE_SIZE
        found = runs.order_by("-executed_at", "-pk")[start : start + PAGE_SIZE]
        rows, empty = tuple(_row(run) for run in found), ""
    elif not CheckRun.objects.exists():
        rows, empty = (), "none"
    else:
        rows, empty = (), "no_match"

    counts = _status_counts(criteria)
    groups = (
        chip_group("status", "Status", STATUS_CHIPS, criteria, base, counts, STATUS_TONES),
        chip_group("since", "Since", SINCE_CHIPS, criteria, base),
    )
    return CheckList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + criteria.querystring(page=page - 1) if page > 1 else "",
        next_url=base + criteria.querystring(page=page + 1) if page < pages else "",
        groups=groups,
        checkers=checker_choices(),
        hosts=host_choices(criteria.host),
        q=criteria.q,
        checker=criteria.checker,
        host=criteria.host,
        kept=criteria.kept(),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
