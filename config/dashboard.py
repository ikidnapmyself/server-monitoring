"""Dashboard context for the admin index page.

Provides aggregated metrics for the ops console: active incidents,
pipeline health, recent check runs, and 7-day trend data.
"""

from django.db.models import Count, Q
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
    from apps.console.queries.fleet import build_fleet_metrics
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
