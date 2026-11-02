"""Panels for the console node page.

Every panel is a plain function returning a plain dataclass, so the whole page
is testable without a request. ``apps/console/queries/node.py`` reads them.

Nothing here writes. Nothing here knows about requests.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.timesince import timesince

from apps.alerts.check_integration import STATUS_TO_SEVERITY
from apps.alerts.describing import alert_link
from apps.alerts.identity import local_hostname, local_instance_id
from apps.alerts.reevaluation import PRIMARY_METRIC
from apps.checkers.admin_charts import render_sparkline
from apps.checkers.checkers import CheckStatus
from apps.checkers.models import CheckRun, PreflightRun
from config.describing import Action
from config.readiness import NODE_RECENT_MINUTES
from config.tones import tone_for

# The header chip's tone, derived from the freshness verdict rather than stored
# beside it so the two cannot disagree. "info" is the local node, whose self-check
# age is reported, not judged, so it gets the tone that carries no verdict.
FRESHNESS_TONES = {"ok": "ok", "warn": "warning"}


@dataclass(frozen=True)
class Identity:
    is_local: bool
    role_label: str
    freshness_status: str  # "ok" | "warn" | "info"
    freshness_label: str

    @property
    def freshness_tone(self) -> str:
        return tone_for(FRESHNESS_TONES, self.freshness_status)


def build_identity(node) -> Identity:
    """Role and freshness for the header.

    A peer reuses the dashboard's own window rather than restating a number, so a
    peer that reads amber on the dashboard reads amber here. This instance's own
    row is left out of that verdict for the reason spelled out above the nodes
    card in ``config/readiness.py``: its last_seen tracks local check runs, not a
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


# The metric each numeric checker is judged on comes from the severity
# re-evaluator: one map, so the state table, the charts and the hub-side
# scoring all read the same field of the same checker.


# The state table is two-sourced: CheckRun.status for the local node, Alert.severity
# for a peer. Those are different vocabularies, so a healthy checker read "ok" on
# the hub's page and "info" on a peer's, under one column header. Peers are mapped
# back to the checker's words here, derived from the forward map rather than
# hand-written so the two cannot drift apart.
#
# The inverse is deliberately lossy. CheckStatus.WARNING and CheckStatus.UNKNOWN
# both map forward to AlertSeverity.WARNING, so the pair cannot be told apart in a
# peer's alert row. UNKNOWN is dropped from the inverse on purpose: an incoming
# WARNING is far more often a real warning, and "unknown" is simply not recoverable
# from what a hub holds about a peer. Excluding it by name rather than relying on
# which key the dict comprehension writes last keeps that choice independent of the
# order the forward map happens to be written in. CheckStatus.SKIPPED is dropped for
# the same reason: it shares AlertSeverity.INFO with OK, and a peer's info alert is
# an OK reading, not a skip.
SEVERITY_TO_CHECK_STATUS: dict[str, str] = {
    severity: status.value
    for status, severity in STATUS_TO_SEVERITY.items()
    if status not in (CheckStatus.UNKNOWN, CheckStatus.SKIPPED)
}


def _peer_status(severity: str) -> str:
    """A peer alert's severity in the checker vocabulary the column is headed in.

    Alert.severity is fed by webhook payloads, so a checker-labelled alert can
    still carry a word no checker emits. That reads as "unknown", which is the
    vocabulary's own word for a state this hub cannot name, rather than raising on
    the page an operator opened to investigate the node.
    """
    return SEVERITY_TO_CHECK_STATUS.get(severity, CheckStatus.UNKNOWN.value)


@dataclass(frozen=True)
class CheckerRow:
    checker: str
    status: str
    value: str
    observed_at: object
    action: Action


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
                action=Action("Open the check run", "view_check_run", {"pk": run.pk}),
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
    part in (a re-evaluation, an incident relink) and it
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
                status=_peer_status(alert.severity),
                value=_format_metric(raw),
                observed_at=alert.received_at,
                action=alert_link(alert.pk),
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
    run: PreflightRun | None
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
