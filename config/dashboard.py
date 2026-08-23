"""Dashboard context for the admin index page.

Provides aggregated metrics for the ops console: active incidents,
pipeline health, recent check runs, and 7-day trend data.
"""

import json
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.html import format_html

NODE_RECENT_MINUTES = 15


def prettify_json(data):
    """Render a JSON-serializable value as a syntax-highlighted <pre> block."""
    if data is None:
        return "-"
    formatted = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return format_html(
        '<pre style="background:var(--body-bg, #f8f9fa);color:var(--body-fg, #333);'
        "border:1px solid var(--hairline-color, #ddd);"
        "padding:10px;border-radius:4px;"
        'max-height:400px;overflow:auto;font-size:13px;margin:0;">{}</pre>',
        formatted,
    )


def build_readiness():
    """Configuration-readiness signals for the dashboard.

    Each entry: {key, label, status (ok|info|warn|error|neutral), detail, url}.
    Pure read-aggregation; safe on empty tables.
    """
    from datetime import timedelta

    from django.urls import reverse
    from django.utils import timezone

    from apps.alerts.models import Node
    from apps.checkers.models import PreflightRun
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.inbox import DEFAULT_STALE_MINUTES
    from apps.orchestration.models import PipelineDefinition, PipelineRun, PipelineStatus

    now = timezone.now()
    out = []

    # Which lanes promise delivery — read before the channel entry below, because
    # whether "no channel" is a fault depends entirely on whether anything asked
    # for one.
    lanes = PipelineDefinition.objects.filter(is_active=True).select_related("channel")
    delivering = [lane for lane in lanes if "notify" in lane.routable_stages()]

    # Channels
    total = NotificationChannel.objects.count()
    active = NotificationChannel.objects.filter(is_active=True).count()
    if active:
        c_status, detail = "ok", f"{active}/{total} channels active"
    elif delivering:
        c_status, detail = (
            "error",
            f"No active channel — {len(delivering)} lane(s) promise delivery",
        )
    else:
        # A hub that reads the admin daily and notifies nobody is a supported
        # setup, not a fault. Painting it red trains operators to ignore the panel.
        c_status, detail = "info", "No channel configured — recording only"
    out.append(
        {
            "key": "channels",
            "label": "Notification channels",
            "status": c_status,
            "detail": detail,
            "url": reverse("admin:notify_notificationchannel_changelist"),
        }
    )

    # Lane delivery. A lane that lists NOTIFY is a promise to deliver;
    # routed_channel() is the one rule for whether it can keep it. Three states,
    # and only one is red: a hub with no channel and no delivering lane never made
    # the promise, and reporting it as a fault would train operators to ignore the
    # panel. See docs/plans/2026-08-22-lane-channel-required-design.md §2.3.
    undeliverable = sorted(lane.name for lane in delivering if lane.routed_channel() is None)
    if undeliverable:
        l_status = "error"
        detail = "{} lane(s) route to notify with no active channel: {}".format(
            len(undeliverable), ", ".join(undeliverable)
        )
    elif delivering:
        l_status = "ok"
        detail = f"{len(delivering)} lane(s) deliver to an active channel"
    elif active:
        # One edit from delivering: a nudge, not an alarm.
        l_status = "ok"
        detail = "Channel active, but no lane delivers to it yet"
    else:
        # Recording only — a supported way to run this hub, not a fault.
        l_status = "info"
        detail = "Recording only — no lane delivers, no channel configured"
    out.append(
        {
            "key": "lane_channels",
            "label": "Lane delivery",
            "status": l_status,
            "detail": detail,
            "url": reverse("admin:orchestration_pipelinedefinition_changelist"),
        }
    )

    # LLM provider
    p_active = IntelligenceProvider.objects.filter(is_active=True).count()
    out.append(
        {
            "key": "provider",
            "label": "LLM provider",
            "status": "ok" if p_active else "error",
            "detail": (
                "Active provider set"
                if p_active
                else "No active provider — analysis falls back to 'no AI'"
            ),
            "url": reverse("admin:intelligence_intelligenceprovider_changelist"),
        }
    )

    # Preflight
    latest = PreflightRun.objects.order_by("-created_at").first()
    if latest is None:
        pf_status, detail = "neutral", "Never run"
    else:
        pf_status = (
            latest.overall_status if latest.overall_status in {"ok", "warn", "error"} else "neutral"
        )
        detail = f"{latest.passed} ok / {latest.warnings} warn / {latest.errors} error"
    out.append(
        {
            "key": "preflight",
            "label": "Preflight",
            "status": pf_status,
            "detail": detail,
            "url": reverse("admin:checkers_preflightrun_changelist"),
        }
    )

    # Inbox
    pending = PipelineRun.objects.filter(status=PipelineStatus.PENDING).count()
    stuck = PipelineRun.objects.filter(
        status=PipelineStatus.PROCESSING,
        updated_at__lt=now - timedelta(minutes=DEFAULT_STALE_MINUTES),
    ).count()
    if stuck:
        i_status, detail = "error", f"{stuck} stuck run(s)"
    elif pending:
        i_status, detail = "warn", f"{pending} pending"
    else:
        i_status, detail = "ok", "Drained"
    out.append(
        {
            "key": "inbox",
            "label": "Inbox",
            "status": i_status,
            "detail": detail,
            "url": reverse("admin:orchestration_inboxitem_changelist"),
        }
    )

    # Nodes
    total_nodes = Node.objects.count()
    recent = Node.objects.filter(
        last_seen__gte=now - timedelta(minutes=NODE_RECENT_MINUTES)
    ).count()
    if total_nodes == 0:
        n_status, detail = "neutral", "No nodes seen"
    elif recent == total_nodes:
        n_status, detail = "ok", f"{recent}/{total_nodes} seen recently"
    elif recent == 0:
        n_status, detail = "warn", f"No node seen in {NODE_RECENT_MINUTES} min"
    else:
        n_status, detail = "warn", f"{recent}/{total_nodes} seen recently"
    out.append(
        {
            "key": "nodes",
            "label": "Nodes",
            "status": n_status,
            "detail": detail,
            "url": reverse("admin:alerts_node_changelist"),
        }
    )

    return out


def get_dashboard_context():
    """Build template context dict for the admin dashboard."""
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
        pipeline_qs.values_list("status").annotate(count=Count("id")).values_list("status", "count")
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
        "readiness": build_readiness(),
    }
