"""Panels for the Node admin detail page.

Every panel is a plain function returning a plain dataclass, so the whole page
is testable without driving the admin. ``NodeAdmin.render_change_form`` calls
``build_node_overview`` and the change_form template renders the result.

Nothing here writes. Nothing here knows about requests.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.timesince import timesince

from apps.alerts.identity import local_hostname, local_instance_id
from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
from apps.alerts.reevaluation import PRIMARY_METRIC
from apps.checkers.admin_charts import render_sparkline
from apps.checkers.models import CheckRun, PreflightRun
from config.dashboard import NODE_RECENT_MINUTES

SEVERITY_COLORS: dict[str, str] = {
    AlertSeverity.CRITICAL: "#dc3545",
    AlertSeverity.WARNING: "#ffc107",
    AlertSeverity.INFO: "#17a2b8",
}

# Worst first: the order both the changelist column and the detail header read in.
SEVERITIES_WORST_FIRST = [AlertSeverity.CRITICAL, AlertSeverity.WARNING, AlertSeverity.INFO]

# An acknowledged incident is a live problem someone has picked up. Counting it
# as handled would make a node look healthy the moment an operator touched it.
UNRESOLVED_INCIDENT_STATUSES = [IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]


@dataclass(frozen=True)
class Identity:
    is_local: bool
    role_label: str
    freshness_status: str  # "ok" | "warn" | "info"
    freshness_label: str


def build_identity(node) -> Identity:
    """Role and freshness for the header.

    A peer reuses the dashboard's own window rather than restating a number, so a
    peer that reads amber on the dashboard reads amber here. This instance's own
    row is left out of that verdict for the reason spelled out above the nodes
    card in ``config/dashboard.py``: its last_seen tracks local check runs, not a
    machine still reaching us, so a window applied to it paints a healthy install
    amber forever. Its age is reported as information instead.
    """
    is_local = node.instance_id == local_instance_id()
    now = timezone.now()
    age = timesince(node.last_seen, now)
    if is_local:
        return Identity(
            is_local=True,
            role_label="This hub",
            freshness_status="info",
            freshness_label=f"self-check {age} ago",
        )
    cutoff = now - timedelta(minutes=NODE_RECENT_MINUTES)
    return Identity(
        is_local=False,
        role_label="Peer",
        freshness_status="ok" if node.last_seen >= cutoff else "warn",
        freshness_label=f"{age} ago",
    )


def unresolved_counts(node) -> dict[str, int]:
    """Unresolved incidents per severity for one node.

    Prefers the annotations ``NodeAdmin.get_queryset`` adds, so rendering the
    whole changelist stays one query. Falls back to a per-node aggregate for a
    node that arrived unannotated. Both paths count incidents, not alerts: an
    incident this node raised six alerts on is one incident, not six.
    """
    if hasattr(node, "unresolved_total"):
        return {s: getattr(node, f"unresolved_{s}", 0) for s in SEVERITIES_WORST_FIRST}
    rows = (
        Incident.objects.filter(alerts__node=node, status__in=UNRESOLVED_INCIDENT_STATUSES)
        .values("severity")
        .annotate(count=Count("pk", distinct=True))
    )
    counted = {row["severity"]: row["count"] for row in rows}
    return {s: counted.get(s, 0) for s in SEVERITIES_WORST_FIRST}


def render_severity_chips(node) -> str:
    """Linked severity chips, worst first. A quiet node reads as a dash.

    Each chip links to the incident changelist already narrowed to this node and
    severity, reusing the ``alerts__node`` filter rather than a parallel view.
    """
    counts = unresolved_counts(node)
    parts = []
    for severity in SEVERITIES_WORST_FIRST:
        count = counts.get(severity, 0)
        if not count:
            continue
        url = "{}?alerts__node__id__exact={}&status__in={}&severity__exact={}".format(
            reverse("admin:alerts_incident_changelist"),
            node.pk,
            ",".join(UNRESOLVED_INCIDENT_STATUSES),
            severity,
        )
        parts.append(
            format_html(
                '<a href="{}" style="background-color: {}; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px; text-decoration: none;">{} {}</a>',
                url,
                SEVERITY_COLORS.get(severity, "#6c757d"),
                count,
                severity.upper(),
            )
        )
    if not parts:
        return "—"
    return format_html_join(" ", "{}", ((part,) for part in parts))


# The metric each numeric checker is judged on comes from the severity
# re-evaluator: one map, so the state table, the charts and the hub-side
# scoring all read the same field of the same checker.


@dataclass(frozen=True)
class CheckerRow:
    checker: str
    status: str
    value: str
    observed_at: object
    url: str


def _format_metric(raw) -> str:
    """One display string for a metric value, whatever type it arrived as.

    Peer values come off ``Alert.annotations`` as strings; local values come off
    ``CheckRun.metrics`` as numbers. Anything missing or unparseable reads as a
    dash, never as a stack trace on an operator's page.
    """
    if raw is None or raw == "" or isinstance(raw, bool):
        return "—"
    try:
        return f"{float(raw):.1f}"
    except (TypeError, ValueError):
        return str(raw)


def _json_dict(value) -> dict:
    """A JSON field read as a dict, whatever it actually holds.

    ``Alert.labels``, ``Alert.annotations`` and ``CheckRun.metrics`` are JSON
    fields fed by attacker-controlled webhook payloads, so any of them can be a
    string. Same defence as ``instance_key_from_labels`` in services.py: treat
    anything non-dict as empty rather than letting ``.get`` raise and 500 the
    page an operator opened to investigate that very node.
    """
    return value if isinstance(value, dict) else {}


def _check_hostname(node) -> str:
    """The hostname to match this machine's CheckRun rows on.

    ``Node.hostname`` is optional and ``Node.upsert`` only writes it when truthy,
    so the local row can carry the blank default. Matching CheckRun on a blank
    hostname matches nothing, and the page then reports that the machine has
    never run a checker while the severity chips beside it show its live
    incidents. The caller already knows this node is the local one, so the
    machine's own name is available and is the honest answer. A recorded
    hostname still wins: only the blank case falls through.
    """
    return node.hostname or local_hostname()


def _local_checker_rows(node) -> list[CheckerRow]:
    """Newest CheckRun per checker for the machine this hub runs on.

    Two steps on purpose. Walking the table to keep the first row per checker
    has no LIMIT and no early exit: CheckRun has no retention anywhere in this
    repo, and a host checking every five minutes writes thousands of rows a day,
    so that walk grows without bound behind an operator's page. Instead: one
    cheap ``distinct`` to learn which checkers this host has ever reported, then
    one indexed newest-row query each. The names still come from the data rather
    than a static map, so a checker nobody listed still shows up.
    """
    rows: list[CheckerRow] = []
    # order_by() clears the model's default ordering: left on, executed_at joins
    # the SELECT and every row comes back distinct, which is no distinct at all.
    hostname = _check_hostname(node)
    names = (
        CheckRun.objects.filter(hostname=hostname)
        .order_by()
        .values_list("checker_name", flat=True)
        .distinct()
    )
    # ``[:1]`` rather than ``.first()``: the name came out of the table, so there
    # is always a row, and iterating says that without an unreachable None branch.
    newest = [
        run
        for name in names
        for run in CheckRun.objects.filter(hostname=hostname, checker_name=name).order_by(
            "-executed_at"
        )[:1]
    ]
    for run in newest:
        metric = PRIMARY_METRIC.get(run.checker_name)
        raw = _json_dict(run.metrics).get(metric) if metric else None
        rows.append(
            CheckerRow(
                checker=run.checker_name,
                status=run.status,
                value=_format_metric(raw),
                observed_at=run.executed_at,
                url=reverse("admin:checkers_checkrun_change", args=[run.pk]),
            )
        )
    return sorted(rows, key=lambda r: r.checker)


def _peer_checker_rows(node) -> list[CheckerRow]:
    """A peer has no CheckRun here — its current state is its Alert rows.

    One Alert per checker per node by fingerprint, updated in place on every
    push, so the newest write is the whole history this hub holds.

    Filtered in the database rather than in Python: checker alerts are bounded
    by fingerprint dedup, but a node's webhook alerts are not, and walking all
    of them only to discard them is work the page does not need.

    The timestamp is ``received_at``, and the column is headed "First seen" to
    match. Nothing on Alert records when a peer last repeated an observation:
    ``updated_at`` is auto_now, so it bumps on hub-side writes the peer had no
    part in (the re-evaluate action, an admin save, an incident relink) and it
    does NOT bump on a quiet re-push, which _process_alert returns early on
    without saving. ``started_at`` is the peer's own clock, stamped once at
    creation and never refreshed on a later push. ``received_at`` is the only
    one that means arrival, and it can only understate freshness, never
    overstate it. Whether the machine itself is still reporting is a node-level
    question, answered by Node.last_seen in the header chip above.
    """
    rows: list[CheckerRow] = []
    for alert in node.alerts.filter(labels__has_key="checker").order_by("-updated_at"):
        checker = _json_dict(alert.labels).get("checker")
        if not checker:
            continue  # a webhook alert is not a checker result
        if any(r.checker == checker for r in rows):
            continue
        metric = PRIMARY_METRIC.get(checker)
        raw = _json_dict(alert.annotations).get(metric) if metric else None
        rows.append(
            CheckerRow(
                checker=checker,
                status=alert.severity,
                value=_format_metric(raw),
                observed_at=alert.received_at,
                url=reverse("admin:alerts_alert_change", args=[alert.pk]),
            )
        )
    return sorted(rows, key=lambda r: r.checker)


def build_checker_rows(node, identity: Identity | None = None) -> list[CheckerRow]:
    """Current per-checker state, from whichever source this node has.

    ``identity`` is optional so a caller with one already computed can hand it
    over: ``build_identity`` calls ``socket.gethostname()``, and the page needs
    the same answer five times.
    """
    if (identity or build_identity(node)).is_local:
        return _local_checker_rows(node)
    return _peer_checker_rows(node)


LOCAL_TIME_LABEL = "Observed"
PEER_TIME_LABEL = "First seen"


def checker_time_label(node, identity: Identity | None = None) -> str:
    """Header for the checker table's timestamp column.

    The two sides do not hold the same fact, so they must not claim the same
    one. A local row is a CheckRun and ``executed_at`` really is the last
    reading. A peer row is an Alert, and the best it can offer is when the
    observation first arrived — see ``_peer_checker_rows``.
    """
    if (identity or build_identity(node)).is_local:
        return LOCAL_TIME_LABEL
    return PEER_TIME_LABEL


@dataclass(frozen=True)
class IncidentRow:
    title: str
    severity: str
    status: str
    color: str
    created_at: object
    url: str


def build_incident_rows(node, limit: int = 10) -> list[IncidentRow]:
    """The node's newest incidents. The chips give counts; this gives names.

    ``distinct()`` because an incident this node raised six alerts on must appear
    once, not six times.
    """
    incidents = (
        Incident.objects.filter(alerts__node=node).distinct().order_by("-created_at")[:limit]
    )
    return [
        IncidentRow(
            title=incident.title,
            severity=incident.severity,
            status=incident.status,
            color=SEVERITY_COLORS.get(incident.severity, "#6c757d"),
            created_at=incident.created_at,
            url=reverse("admin:alerts_incident_change", args=[incident.pk]),
        )
        for incident in incidents
    ]


# Charts read the same primary metric the state table reads.
CHART_SPECS = [
    ("Disk usage", "disk"),
    ("CPU", "cpu"),
    ("Memory", "memory"),
]

CHART_HISTORY_LIMIT = 50

PEER_HISTORY_NOTE = (
    "Metric history is written by the machine that runs the checker and is "
    "not pushed to a hub, so there is nothing to plot here yet."
)


@dataclass(frozen=True)
class Chart:
    title: str
    svg: object
    latest: str


def build_charts(node, identity: Identity | None = None) -> list[Chart]:
    """Time series for the local node only.

    A peer has no CheckRun rows on this hub, so it gets an empty list and the
    template shows ``charts_note`` instead. A blank chart would read as "flat",
    which is a lie.
    """
    if not (identity or build_identity(node)).is_local:
        return []
    hostname = _check_hostname(node)
    charts: list[Chart] = []
    for title, checker in CHART_SPECS:
        metric = PRIMARY_METRIC[checker]
        runs = list(
            CheckRun.objects.filter(hostname=hostname, checker_name=checker).order_by(
                "-executed_at"
            )[:CHART_HISTORY_LIMIT]
        )
        runs.reverse()  # newest N, restored to oldest -> newest for plotting
        points: list[tuple[float, float]] = []
        markers: list[float] = []
        for index, run in enumerate(runs):
            value = _json_dict(run.metrics).get(metric)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            points.append((index, float(value)))
            if run.alert_id is not None:
                markers.append(index)
        if not points:
            continue
        charts.append(
            Chart(
                title=title,
                svg=render_sparkline(
                    points,
                    markers=markers,
                    width=260,
                    height=64,
                    title=title,
                    show_axis=True,
                ),
                latest=_format_metric(points[-1][1]),
            )
        )
    return charts


def charts_note(node, identity: Identity | None = None) -> str:
    """Why a peer has no charts. Empty for the local node."""
    return "" if (identity or build_identity(node)).is_local else PEER_HISTORY_NOTE


PEER_PREFLIGHT_NOTE = (
    "Preflight is node-local and is not pushed to a hub, so this hub never "
    "holds a peer's preflight run."
)


@dataclass(frozen=True)
class PreflightPanel:
    run: object
    note: str


def build_preflight(node, identity: Identity | None = None) -> PreflightPanel:
    """Latest preflight for the local node; an explanation for a peer.

    Matched on ``instance_id`` because PreflightRun has no node FK. Both writers
    now spell this machine the same way — see the 0003 backfill migration for
    the rows written before they did.
    """
    if not (identity or build_identity(node)).is_local:
        return PreflightPanel(run=None, note=PEER_PREFLIGHT_NOTE)
    run = PreflightRun.objects.filter(instance_id=node.instance_id).order_by("-created_at").first()
    if run is None:
        return PreflightPanel(run=None, note="No preflight recorded on this machine yet.")
    return PreflightPanel(run=run, note="")


@dataclass(frozen=True)
class PipelineRow:
    run_id: str
    origin: str
    status: str
    created_at: object
    url: str


def build_pipeline_rows(node, limit: int = 10) -> list[PipelineRow]:
    """The node's newest pipeline runs, each admin-linked."""
    return [
        PipelineRow(
            run_id=run.run_id,
            origin=run.origin,
            status=run.status,
            created_at=run.created_at,
            url=reverse("admin:orchestration_pipelinerun_change", args=[run.pk]),
        )
        for run in node.pipeline_runs.order_by("-created_at")[:limit]
    ]


@dataclass(frozen=True)
class NodeOverview:
    identity: Identity
    chips: object
    checker_rows: list[CheckerRow]
    checker_time_label: str
    incident_rows: list[IncidentRow]
    charts: list[Chart]
    charts_note: str
    preflight: PreflightPanel
    pipeline_rows: list[PipelineRow]


def build_node_overview(node) -> NodeOverview:
    """Every panel on the node detail page, in one object for the template."""
    identity = build_identity(node)
    return NodeOverview(
        identity=identity,
        chips=render_severity_chips(node),
        checker_rows=build_checker_rows(node, identity),
        checker_time_label=checker_time_label(node, identity),
        incident_rows=build_incident_rows(node),
        charts=build_charts(node, identity),
        charts_note=charts_note(node, identity),
        preflight=build_preflight(node, identity),
        pipeline_rows=build_pipeline_rows(node),
    )
