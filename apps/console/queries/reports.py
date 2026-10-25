"""Read model for the Reports summary. Returns dataclasses, never querysets.

The admin dashboard reads the same functions until PR 11 deletes it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, Sum

from apps.alerts.describing import node_name
from apps.checkers.checkers import checker_label
from apps.checkers.models import CheckRun, PreflightRun
from apps.checkers.preflight_describing import describe_preflight_run
from apps.console.filters import CHECK_STATUS_CHOICES, RUN_STATUS_CHOICES, RunFilter
from apps.console.intents import (
    ANALYSIS_READ,
    CHECK_READ,
    NODE_READ,
    PREFLIGHT_READ,
    RUN_READ,
)
from apps.console.queries.runs import reason_label
from apps.intelligence.describing import provider_label
from apps.intelligence.models import AnalysisRun
from apps.orchestration.describing import REASONS
from apps.orchestration.models import PipelineRun
from config.admin_badges import CHECK_STATUS_TONES
from config.dashboard import METRIC_UNITS, build_fleet_metrics
from config.describing import Action, Description, format_percent

DAY = timedelta(hours=24)
WEEK = timedelta(days=7)
TOP = 5
HEALTH = (("finished", "Finished"), ("failed", "Failed"), ("active", "Still going"))


@dataclass(frozen=True)
class ReportRow:
    label: str
    count: str
    link: Action


@dataclass(frozen=True)
class PipelineHealth:
    headline: str
    rows: tuple[ReportRow, ...]


@dataclass(frozen=True)
class FleetCell:
    value: str
    tone: str


@dataclass(frozen=True)
class FleetRow:
    pk: int
    link: Action
    cells: tuple[FleetCell, ...]


@dataclass(frozen=True)
class Fleet:
    columns: tuple[str, ...]
    rows: tuple[FleetRow, ...]


@dataclass(frozen=True)
class Reports:
    health: PipelineHealth | None
    reasons: tuple[ReportRow, ...] | None
    checkers: tuple[ReportRow, ...] | None
    providers: tuple[ReportRow, ...] | None
    preflight: Description | None
    fleet: Fleet | None


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def pipeline_health(now: datetime) -> PipelineHealth:
    counts = PipelineRun.objects.filter(created_at__gte=now - DAY).aggregate(
        total=Count("pk"),
        **{key: Count("pk", filter=RUN_STATUS_CHOICES[key]) for key, _ in HEALTH},
    )
    total = counts["total"]
    if not total:
        return PipelineHealth("No runs in the last 24 hours.", ())
    rows = tuple(
        ReportRow(
            label, f"{counts[key]:,}", Action(label, "view_runs", {"status": key, "since": "24h"})
        )
        for key, label in HEALTH
    )
    rate = format_percent(counts["finished"] / total * 100)
    return PipelineHealth(f"{rate} of runs finished", rows)


def failing_checkers(now: datetime) -> tuple[ReportRow, ...]:
    found = (
        CheckRun.objects.filter(
            status__in=CHECK_STATUS_CHOICES["failing"], executed_at__gte=now - WEEK
        )
        .values("checker_name")
        .annotate(count=Count("pk"))
        .order_by("-count", "checker_name")[:TOP]
    )
    return tuple(
        ReportRow(
            checker_label(row["checker_name"]),
            _plural(row["count"], "failing result", "failing results"),
            Action(
                checker_label(row["checker_name"]),
                "view_checks",
                {"checker": row["checker_name"], "status": "failing", "since": "7d"},
            ),
        )
        for row in found
    )


def failure_reasons(now: datetime) -> tuple[ReportRow, ...]:
    """Failed runs by the reason ``reason_code`` reads, the way the Runs ``reason`` chip does."""
    reasons = ("other", *REASONS)
    counts = PipelineRun.objects.filter(created_at__gte=now - WEEK).aggregate(
        **{
            reason: Count("pk", filter=RunFilter(status="failed", reason=reason).matches())
            for reason in reasons
        }
    )
    found = sorted(
        ((reason, counts[reason]) for reason in reasons if counts[reason]),
        key=lambda pair: -pair[1],
    )[:TOP]
    return tuple(
        ReportRow(
            reason_label(reason),
            _plural(count, "failed run", "failed runs"),
            Action(
                reason_label(reason),
                "view_runs",
                {"status": "failed", "reason": reason, "since": "7d"},
            ),
        )
        for reason, count in found
    )


def provider_usage(now: datetime) -> tuple[ReportRow, ...]:
    found = (
        AnalysisRun.objects.filter(created_at__gte=now - WEEK)
        .values("provider")
        .annotate(runs=Count("pk"), tokens=Sum("total_tokens"))
        .order_by("-runs", "provider")
    )
    return tuple(
        ReportRow(
            provider_label(row["provider"]),
            f"{_plural(row['runs'], 'analysis', 'analyses')}, "
            f"{_plural(row['tokens'] or 0, 'token', 'tokens')}",
            Action(
                provider_label(row["provider"]),
                "view_analyses",
                {"provider": row["provider"], "since": "7d"},
            ),
        )
        for row in found
    )


def latest_preflight() -> Description:
    run = PreflightRun.objects.order_by("-created_at", "-pk").first()
    if run is None:
        return Description("Preflight hasn't run on this hub yet.", "muted")
    described = describe_preflight_run(run)
    return Description(
        described.headline,
        described.tone,
        action=Action("Open the preflight run", "view_preflight_run", {"pk": run.pk}),
    )


def fleet() -> Fleet:
    """The fleet grid, headed and named the console's way: checker labels, never IDs."""
    metrics = build_fleet_metrics()
    return Fleet(
        columns=tuple(
            f"{checker_label(column.checker)} ({METRIC_UNITS[column.checker]})"
            for column in metrics.columns
        ),
        rows=tuple(
            FleetRow(
                pk=row.pk,
                link=Action(node_name(row.hostname), "view_node", {"pk": row.pk}),
                cells=tuple(
                    (
                        FleetCell(cell.value, CHECK_STATUS_TONES.get(cell.status, "muted"))
                        if cell.status
                        else FleetCell("", "")
                    )
                    for cell in row.cells
                ),
            )
            for row in metrics.rows
        ),
    )


def _reads(user, permissions: tuple[str, ...]) -> bool:
    return any(user.has_perm(permission) for permission in permissions)


def build_reports(user, now: datetime) -> Reports:
    """Every card the user can read. A card for a model they can't read is None."""
    runs = _reads(user, RUN_READ)
    return Reports(
        health=pipeline_health(now) if runs else None,
        reasons=failure_reasons(now) if runs else None,
        checkers=failing_checkers(now) if _reads(user, CHECK_READ) else None,
        providers=provider_usage(now) if _reads(user, ANALYSIS_READ) else None,
        preflight=latest_preflight() if _reads(user, PREFLIGHT_READ) else None,
        fleet=fleet() if _reads(user, NODE_READ) else None,
    )
