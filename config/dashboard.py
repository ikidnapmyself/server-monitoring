"""Dashboard context for the admin index page.

Provides aggregated metrics for the ops console: active incidents,
pipeline health, recent check runs, and 7-day trend data.
"""

from dataclasses import dataclass

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from config.readiness import build_readiness


def _row(row, user) -> dict:
    """A Reports row as the dashboard template reads it. No url when the user can't open it."""
    from apps.console.intents import resolve_action

    url = resolve_action(row.link, user).url
    return {"label": row.label, "count": row.count, "url": url}


def get_dashboard_context(user):
    """Build template context dict for the admin dashboard."""
    from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
    from apps.checkers.models import CheckRun
    from apps.console.queries.reports import (
        failing_checkers,
        failure_reasons,
        pipeline_health,
        provider_usage,
    )
    from apps.orchestration.models import PipelineRun, PipelineStatus

    now = timezone.now()

    # --- Active Incidents ---
    active_qs = Incident.objects.filter(
        status__in=[IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]
    )
    active_incidents = active_qs.aggregate(
        total=Count("id"),
        critical=Count("id", filter=Q(severity=AlertSeverity.CRITICAL)),
        warning=Count("id", filter=Q(severity=AlertSeverity.WARNING)),
        info=Count("id", filter=Q(severity=AlertSeverity.INFO)),
    )

    # --- Pipeline Health (24h) ---
    health = pipeline_health(now)
    health_card = {"headline": health.headline, "rows": [_row(row, user) for row in health.rows]}

    # --- Recent Check Runs (last 10) ---
    recent_check_runs = list(
        CheckRun.objects.order_by("-executed_at").only(
            "checker_name", "hostname", "status", "executed_at"
        )[:10]
    )

    # --- Failed Pipelines (last 5) ---
    failed_pipelines = list(
        PipelineRun.objects.filter(status=PipelineStatus.FAILED)
        .order_by("-created_at")
        .only(
            "id",
            "run_id",
            "trace_id",
            "last_error_type",
            "last_error_message",
            "created_at",
        )[:5]
    )

    # --- 7-Day Aggregations ---
    # The console's Reports summary reads the same rows, and each row opens its console list.
    top_failing_checkers = [_row(row, user) for row in failing_checkers(now)]
    top_failure_reasons = [_row(row, user) for row in failure_reasons(now)]
    usage = [_row(row, user) for row in provider_usage(now)]

    return {
        "active_incidents": active_incidents,
        "pipeline_health": health_card,
        "recent_check_runs": recent_check_runs,
        "failed_pipelines": failed_pipelines,
        "top_failing_checkers": top_failing_checkers,
        "failure_reasons": top_failure_reasons,
        "provider_usage": usage,
        "readiness": build_readiness(),
        "fleet_metrics": build_fleet_metrics(),
    }


# The unit each headline metric is read in. Keyed by checker rather than by
# metric name because that is what the column is headed with, and the two
# temperature checkers report different metric keys for the same unit.
METRIC_UNITS = {
    "cpu": "%",
    "memory": "%",
    "disk": "%",
    "disk_inodes": "%",
    "io_strain": "%",
    "cpu_temp": "°C",
    "disk_temp": "°C",
}


@dataclass(frozen=True)
class MetricCell:
    """One node's current reading for one checker.

    ``status`` is blank for a checker this node does not report, which is a
    different thing from a reading the hub cannot name. The first gets no
    colour, the second gets "unknown".
    """

    value: str
    status: str


@dataclass(frozen=True)
class NodeMetrics:
    pk: int
    instance_id: str
    hostname: str
    url: str
    cells: list[MetricCell]


@dataclass(frozen=True)
class MetricColumn:
    checker: str
    unit: str


@dataclass(frozen=True)
class FleetMetrics:
    """The numbers the fleet is currently reporting, one row per node.

    ``columns`` is derived from the data, so a checker no node reports is not a
    column of dashes. Empty ``rows`` means no node has reported a headline
    metric yet, which the template says in words.
    """

    columns: list[MetricColumn]
    rows: list[NodeMetrics]


def build_fleet_metrics() -> FleetMetrics:
    """Current headline readings for every node, from whichever source it has.

    Reuses ``node_overview.build_checker_rows``, which is what the Node detail
    page already reads, so a number here and a number there cannot disagree.
    Imported inside the function because ``node_overview`` imports this module.

    Columns are the checkers with a ``PRIMARY_METRIC``: those are the ones that
    reduce to a single number, which is the only kind a grid cell can hold.

    Cost is one small group of queries per node, the same group the Node page
    pays for one node. The fleet is a handful of machines pushing to one hub,
    so this is bounded by the node table, not by alert or check history.
    """
    from apps.alerts.models import Node
    from apps.alerts.node_overview import build_checker_rows
    from apps.alerts.reevaluation import PRIMARY_METRIC

    headline = sorted(PRIMARY_METRIC)
    readings = {}
    for node in Node.objects.order_by("instance_id"):
        readings[node] = {
            row.checker: row for row in build_checker_rows(node) if row.checker in headline
        }
    checkers = [name for name in headline if any(name in r for r in readings.values())]
    rows = [
        NodeMetrics(
            pk=node.pk,
            instance_id=node.instance_id,
            hostname=node.hostname,
            url=reverse("console:node", args=[node.pk]),
            cells=[
                (
                    MetricCell(value=found.value, status=found.status)
                    if (found := reading.get(name))
                    else MetricCell(value="—", status="")
                )
                for name in checkers
            ],
        )
        for node, reading in readings.items()
        if reading
    ]
    return FleetMetrics(
        columns=[MetricColumn(checker=name, unit=METRIC_UNITS[name]) for name in checkers],
        rows=rows,
    )
