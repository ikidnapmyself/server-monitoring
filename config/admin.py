"""Custom admin site for the server monitoring ops console."""

from datetime import timedelta

from django.contrib.admin import AdminSite
from django.db.models import Count, Q, Sum
from django.utils import timezone


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring"
    site_title = "Server Monitoring"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"

    def index(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(self._get_dashboard_context())
        return super().index(request, extra_context=extra_context)

    def _get_dashboard_context(self):
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
        from apps.checkers.models import CheckRun, CheckStatus
        from apps.intelligence.models import AnalysisRun
        from apps.orchestration.models import PipelineRun, PipelineStatus

        now = timezone.now()
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)

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
        pipeline_qs = PipelineRun.objects.filter(created_at__gte=last_24h)
        status_counts = dict(
            pipeline_qs.values_list("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )
        total_runs = sum(status_counts.values())
        successful = status_counts.get(PipelineStatus.NOTIFIED, 0)
        in_flight_statuses = [
            PipelineStatus.PENDING,
            PipelineStatus.INGESTED,
            PipelineStatus.CHECKED,
            PipelineStatus.ANALYZED,
        ]
        pipeline_health = {
            "total": total_runs,
            "successful": successful,
            "failed": status_counts.get(PipelineStatus.FAILED, 0),
            "retrying": status_counts.get(PipelineStatus.RETRYING, 0),
            "in_flight": sum(status_counts.get(s, 0) for s in in_flight_statuses),
            "success_rate": round(successful / total_runs * 100, 1) if total_runs else 0,
        }

        # --- Recent Check Runs (last 10) ---
        recent_check_runs = list(
            CheckRun.objects.order_by("-executed_at").only(
                "checker_name", "hostname", "status", "message", "executed_at"
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
        top_failing_checkers = list(
            CheckRun.objects.filter(
                status__in=[CheckStatus.WARNING, CheckStatus.CRITICAL],
                executed_at__gte=last_7d,
            )
            .values("checker_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        top_error_types = list(
            PipelineRun.objects.filter(
                status=PipelineStatus.FAILED,
                created_at__gte=last_7d,
            )
            .values("last_error_type")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        provider_usage = list(
            AnalysisRun.objects.filter(created_at__gte=last_7d)
            .values("provider")
            .annotate(runs=Count("id"), tokens=Sum("total_tokens"))
            .order_by("-runs")
        )

        return {
            "active_incidents": active_incidents,
            "pipeline_health": pipeline_health,
            "recent_check_runs": recent_check_runs,
            "failed_pipelines": failed_pipelines,
            "top_failing_checkers": top_failing_checkers,
            "top_error_types": top_error_types,
            "provider_usage": provider_usage,
        }
