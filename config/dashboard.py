"""Dashboard context for the admin index page.

Provides aggregated metrics for the ops console: active incidents,
pipeline health, recent check runs, and 7-day trend data.
"""

import json
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify

from config.admin_links import changelist_url
from config.describing import Action, Description

NODE_RECENT_MINUTES = 15


def prettify_json(data):
    """Render a JSON-serializable value as a syntax-highlighted <pre> block."""
    if data is None:
        return "-"
    formatted = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return format_html('<pre class="ops-pre">{}</pre>', formatted)


def build_readiness():
    """Configuration-readiness signals for the dashboard.

    Each entry: {key, label, status (ok|info|warn|error|neutral), detail, url, gaps, counts}.
    ``gaps`` lists (lane name, reason code) and is empty except on lane_channels.
    Pure read-aggregation; safe on empty tables.
    """
    from datetime import timedelta

    from django.urls import reverse
    from django.utils import timezone
    from django.utils.timesince import timesince

    from apps.alerts.identity import local_instance_id
    from apps.alerts.models import Node
    from apps.checkers.models import PreflightRun
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.inbox import stuck as stuck_runs
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
            "gaps": [],
            "counts": {"active": active, "total": total, "delivering": len(delivering)},
        }
    )

    # Lane delivery. A lane that lists NOTIFY is a promise to deliver;
    # delivery_gap() is the one rule for whether it can keep it — an active channel
    # is not enough, since a channel naming an unregistered driver fails the run as
    # no_driver just as surely as no channel at all fails it as no_channel. Three states,
    # and only one is red: a hub with no channel and no delivering lane never made
    # the promise, and reporting it as a fault would train operators to ignore the
    # panel. See docs/plans/2026-08-22-lane-channel-required-design.md §2.3.
    undeliverable = sorted(
        (lane.name, gap) for lane in delivering if (gap := lane.delivery_gap()) is not None
    )
    if undeliverable:
        l_status = "error"
        detail = "{} lane(s) cannot deliver: {}".format(
            len(undeliverable),
            ", ".join(f"{name} ({gap})" for name, gap in undeliverable),
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
            # A gap is per-lane and the changelist cannot filter on one, so a fault
            # goes to the map, which names the gap on each card and links the
            # channel behind it. With nothing wrong, the lane list is the place to
            # edit routing.
            "url": (
                reverse("admin:netmap")
                if l_status == "error"
                else reverse("admin:orchestration_pipelinedefinition_changelist")
            ),
            "gaps": undeliverable,
            "counts": {"delivering": len(delivering)},
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
            "gaps": [],
            "counts": {"active": p_active},
        }
    )

    # Preflight
    latest = PreflightRun.objects.order_by("-created_at").first()
    if latest is None:
        pf_status, detail, pf_counts = "neutral", "Never run", {}
    else:
        pf_status = (
            latest.overall_status if latest.overall_status in {"ok", "warn", "error"} else "neutral"
        )
        detail = f"{latest.passed} ok / {latest.warnings} warn / {latest.errors} error"
        pf_counts = {"passed": latest.passed, "warnings": latest.warnings, "errors": latest.errors}
    out.append(
        {
            "key": "preflight",
            "label": "Preflight",
            "status": pf_status,
            "detail": detail,
            "url": reverse("admin:checkers_preflightrun_changelist"),
            "gaps": [],
            "counts": pf_counts,
        }
    )

    # Inbox
    pending = PipelineRun.objects.filter(status=PipelineStatus.PENDING).count()
    stuck = PipelineRun.objects.filter(stuck_runs()).count()
    inbox_url = reverse("console:inbox")
    if stuck:
        i_status, detail = "error", f"{stuck} stuck run(s)"
        inbox_url = f"{inbox_url}?status=stuck"
    elif pending:
        i_status, detail = "warn", f"{pending} pending"
        inbox_url = f"{inbox_url}?status={PipelineStatus.PENDING}"
    else:
        i_status, detail = "ok", "Drained"
    out.append(
        {
            "key": "inbox",
            "label": "Inbox",
            "status": i_status,
            "detail": detail,
            "url": inbox_url,
            "gaps": [],
            "counts": {"pending": pending, "stuck": stuck},
        }
    )

    # Nodes
    #
    # "Seen recently" means two different things in this table, so it is counted
    # against peers only. For a peer, last_seen is the machine still reaching this
    # hub, and its going quiet is the alarm this entry exists for. This instance's
    # own row is upserted by its local check runs, so it says only "somebody ran a
    # check here" — on a hub checked by hand over SSH that is stale nearly always,
    # and folding it into the count painted a healthy fleet amber forever. A panel
    # that is permanently amber is one operators learn to skip, which costs them
    # the peer that really did stop reporting.
    #
    # Us is identified by instance_id, not by last_source: a machine can be
    # registered by both paths and the last write wins, so last_source names how
    # the row was touched, never whose row it is.
    self_id = local_instance_id()
    self_node = Node.objects.filter(instance_id=self_id).first()
    peers = Node.objects.exclude(instance_id=self_id)
    total_nodes = peers.count()
    recent = peers.filter(last_seen__gte=now - timedelta(minutes=NODE_RECENT_MINUTES)).count()
    if total_nodes == 0 and self_node is None:
        n_status, detail = "neutral", "No nodes seen"
    elif total_nodes == 0:
        # One machine monitoring itself is a complete, correct install — there is
        # no fleet here to be degraded. Report it, do not warn about it.
        n_status, detail = "info", "Standalone — this machine only"
    elif recent == total_nodes:
        n_status, detail = "ok", f"{recent}/{total_nodes} seen recently"
    elif recent == 0:
        n_status, detail = "warn", f"No node seen in {NODE_RECENT_MINUTES} min"
    else:
        n_status, detail = "warn", f"{recent}/{total_nodes} seen recently"
    # Our own last local check, appended as information. The card's detail line is
    # one small line of text, so it gets an age and nothing more, and it never
    # moves the status.
    if self_node is not None:
        detail = f"{detail} · self-check {timesince(self_node.last_seen, now)} ago"
    out.append(
        {
            "key": "nodes",
            "label": "Nodes",
            "status": n_status,
            "detail": detail,
            "url": reverse("console:nodes"),
            "gaps": [],
            "counts": {"total": total_nodes, "recent": recent},
        }
    )

    return out


_FIX_TONES = {"error": "critical", "warn": "warning"}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _keyed(entry: dict, headline: str, tone: str, **fields) -> tuple[str, Description]:
    return entry["key"], Description(headline, tone, **fields)


def _channels(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    routes = _plural(entry["counts"]["delivering"], "route", "routes")
    return [
        _keyed(
            entry,
            f"No active channel, {routes} can't deliver",
            tone,
            action=Action("Activate a notification channel", intent="edit_channel"),
        )
    ]


def _lane_channels(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    from apps.orchestration.describing import REASONS

    return [
        (
            f"{entry['key']}-{slugify(name)}",
            Description(
                f"Route {name} can't deliver: {REASONS[code].headline}",
                tone,
                action=REASONS[code].action,
            ),
        )
        for name, code in entry["gaps"]
        # With no active channel anywhere, the channels entry already said this.
        if not (code == "no_channel" and "channels" in fired)
    ]


def _provider(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    return [
        _keyed(
            entry,
            "No AI provider is active, analysis is skipped",
            tone,
            action=Action("Activate an AI provider", intent="edit_provider"),
        )
    ]


def _preflight(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    word = "error" if entry["status"] == "error" else "warning"
    found = entry["counts"][word + "s"]
    return [
        _keyed(
            entry,
            (
                f"Preflight found {_plural(found, word, word + 's')}"
                if found
                else "Preflight didn't pass"
            ),
            tone,
            action=Action("Open the preflight report", intent="view_preflight"),
        )
    ]


def _inbox(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    counts = entry["counts"]
    if counts["stuck"]:
        headline = f"{_plural(counts['stuck'], 'run is', 'runs are')} stuck"
        action = Action("Open the stuck runs", "view_inbox", {"status": "stuck"})
    else:
        headline = f"{_plural(counts['pending'], 'run is', 'runs are')} waiting to be processed"
        action = Action("Open the waiting runs", "view_inbox", {"status": "pending"})
    return [_keyed(entry, headline, tone, action=action)]


def _nodes(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    counts = entry["counts"]
    return [
        _keyed(
            entry,
            (
                f"No node reported in the last {NODE_RECENT_MINUTES} minutes"
                if counts["recent"] == 0
                else f"{counts['total'] - counts['recent']} of {counts['total']} nodes"
                " stopped reporting"
            ),
            tone,
            action=Action("Open the node list", intent="view_nodes"),
        )
    ]


def _generic(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    return [_keyed(entry, f"{entry['label']} needs attention", tone)]


_DESCRIBERS = {
    "channels": _channels,
    "lane_channels": _lane_channels,
    "provider": _provider,
    "preflight": _preflight,
    "inbox": _inbox,
    "nodes": _nodes,
}


def keyed_readiness() -> tuple[tuple[str, Description], ...]:
    """The readiness entries that need a fix, in panel order, each with its fix.

    Each comes with a key built from its entry, and from the lane for a per-lane gap, so
    it names the same problem from one read to the next. ok, info and neutral entries
    describe nothing: they ask nothing of the operator.
    """
    entries = [e for e in build_readiness() if e["status"] in _FIX_TONES]
    fired = {e["key"] for e in entries}
    return tuple(
        found
        for entry in entries
        for found in _DESCRIBERS.get(entry["key"], _generic)(
            entry, _FIX_TONES[entry["status"]], fired
        )
    )


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
    # Each trend row names something the changelists can filter on, so each row
    # carries the filtered URL rather than leaving an operator to rebuild it.
    top_failing_checkers = [
        dict(row, url=changelist_url(CheckRun, checker_name__exact=row["checker_name"]))
        for row in CheckRun.objects.filter(
            status__in=[CheckStatus.WARNING, CheckStatus.CRITICAL],
            executed_at__gte=last_7d,
        )
        .values("checker_name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    ]

    top_error_types = [
        dict(
            row,
            url=changelist_url(
                PipelineRun,
                status__exact=PipelineStatus.FAILED,
                last_error_type__exact=row["last_error_type"],
            ),
        )
        for row in PipelineRun.objects.filter(
            status=PipelineStatus.FAILED,
            created_at__gte=last_7d,
        )
        .values("last_error_type")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    ]

    provider_usage = [
        dict(row, url=changelist_url(AnalysisRun, provider__exact=row["provider"]))
        for row in AnalysisRun.objects.filter(created_at__gte=last_7d)
        .values("provider")
        .annotate(runs=Count("id"), tokens=Sum("total_tokens"))
        .order_by("-runs")
    ]

    return {
        "active_incidents": active_incidents,
        "pipeline_health": pipeline_health,
        "recent_check_runs": recent_check_runs,
        "failed_pipelines": failed_pipelines,
        "top_failing_checkers": top_failing_checkers,
        "top_error_types": top_error_types,
        "provider_usage": provider_usage,
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
