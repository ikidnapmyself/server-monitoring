"""Configuration readiness: what still needs setting up, and the fix for each fault.

The console Overview reads ``keyed_readiness``. ``apps.alerts`` reads
``NODE_RECENT_MINUTES``, so this lives in ``config`` rather than in the console.
"""

from datetime import timedelta

from django.utils.text import slugify

from config.describing import Action, Description

NODE_RECENT_MINUTES = 15


def build_readiness():
    """Configuration-readiness signals for the console Overview.

    Each entry: {key, label, status (ok|info|warn|error|neutral), detail, url, gaps, counts}.
    ``gaps`` lists (lane name, reason code) and is empty except on lane_channels.
    Pure read-aggregation; safe on empty tables.
    """

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
        # A hub that reads the console daily and notifies nobody is a supported
        # setup, not a fault. Painting it red trains operators to ignore the panel.
        c_status, detail = "info", "No channel configured — recording only"
    out.append(
        {
            "key": "channels",
            "label": "Notification channels",
            "status": c_status,
            "detail": detail,
            "url": reverse("console:channels"),
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
            "url": reverse("console:routes"),
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
                else "No active provider, so analysis uses the built-in local analyzer"
            ),
            "url": reverse("console:providers"),
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
            "url": reverse("console:preflight"),
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
            "No AI provider is active, so analysis uses the built-in local analyzer",
            tone,
            action=Action("Activate an AI provider", intent="edit_provider"),
        )
    ]


def _preflight(entry: dict, tone: str, fired: set[str]) -> list[tuple[str, Description]]:
    from apps.checkers.preflight_describing import preflight_headline

    counts = entry["counts"]
    return [
        _keyed(
            entry,
            preflight_headline(entry["status"], counts["warnings"], counts["errors"]),
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
