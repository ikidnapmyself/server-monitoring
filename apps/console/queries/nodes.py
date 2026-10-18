"""Read model for the node list. Returns dataclasses, never querysets."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, AlertSeverity, Node
from apps.console.filters import NodeFilter
from apps.console.queries.hosts import node_keys, node_name, on_node
from apps.console.queries.incidents import ChipGroup, Choice, chip_group
from apps.console.queries.overview import OPEN_STATUSES, firing_by_node, is_silent, node_badge
from config.admin_badges import SEVERITY_TONES, tone_for
from config.dashboard import NODE_RECENT_MINUTES
from config.describing import Action

SEVERITIES = (AlertSeverity.CRITICAL, AlertSeverity.WARNING, AlertSeverity.INFO)
TONE_RANK = {"critical": 0, "warning": 1, "info": 2, "muted": 3, "ok": 4}
FRESHNESS_CHIPS: tuple[Choice, ...] = (
    ("all", "All", ""),
    ("silent", "Silent", "silent"),
    ("reporting", "Reporting", "reporting"),
)


@dataclass(frozen=True)
class SeverityCount:
    severity: str
    count: int
    tone: str
    link: Action


@dataclass(frozen=True)
class NodeListRow:
    pk: int
    name: str
    role: str
    tone: str
    label: str
    counts: tuple[SeverityCount, ...]
    last_seen: datetime
    url: str


@dataclass(frozen=True)
class NodeList:
    rows: tuple[NodeListRow, ...]
    groups: tuple[ChipGroup, ...]
    q: str
    kept: tuple[tuple[str, str], ...]
    policy: Action
    notice: str
    empty: str
    url: str


def open_incidents_by_node(nodes: Iterable[Node]) -> dict[int, dict[str, set[int]]]:
    """Unresolved incident pks per node and severity, by the incident list's node rule.

    A linked alert counts for its node. An unlinked one counts for every node its instance
    labels name, as ``on_node`` matches.
    """
    nodes = list(nodes)
    named: defaultdict[str, set[int]] = defaultdict(set)
    for node in nodes:
        for key in node_keys(node):
            named[key].add(node.pk)
    found: defaultdict[int, defaultdict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    alerts = Alert.objects.filter(incident__status__in=OPEN_STATUSES)
    if len(nodes) == 1:
        alerts = alerts.filter(on_node(nodes[0].pk, node_keys(nodes[0])))
    rows = alerts.values_list("incident_id", "incident__severity", "node_id", "labels").order_by()
    for incident_pk, severity, node_pk, labels in rows:
        owners = {node_pk} if node_pk is not None else set()
        if node_pk is None and isinstance(labels, dict):
            for name in ("instance_id", "instance", "hostname"):
                value = labels.get(name)
                if isinstance(value, str):
                    owners |= named.get(value, set())
        for owner in owners:
            found[owner][severity].add(incident_pk)
    return {pk: dict(by_severity) for pk, by_severity in found.items()}


def severity_counts(node_pk: int, by_severity: dict[str, set[int]]) -> tuple[SeverityCount, ...]:
    return tuple(
        SeverityCount(
            severity=str(severity),
            count=len(by_severity[severity]),
            tone=tone_for(SEVERITY_TONES, severity),
            link=Action(
                f"{len(by_severity[severity])} {severity}",
                "view_incidents",
                {"severity": str(severity), "node": node_pk},
            ),
        )
        for severity in SEVERITIES
        if by_severity.get(severity)
    )


def build_node_list(criteria: NodeFilter) -> NodeList:
    base = reverse("console:nodes")
    cutoff = timezone.now() - timedelta(minutes=NODE_RECENT_MINUTES)
    self_id = local_instance_id()
    nodes = Node.objects.order_by()
    if criteria.q:
        nodes = nodes.filter(
            Q(hostname__icontains=criteria.q) | Q(instance_id__icontains=criteria.q)
        )
    found = list(nodes)
    silent = {node.pk for node in found if is_silent(node, cutoff, self_id)}
    counts = {"all": len(found), "silent": len(silent), "reporting": len(found) - len(silent)}
    if criteria.freshness:
        want = criteria.freshness == "silent"
        found = [node for node in found if (node.pk in silent) == want]
    firing, critical, warning = firing_by_node()
    open_by_node = open_incidents_by_node(found)
    rows = []
    for node in found:
        tone, label = node_badge(
            critical[node.pk], warning[node.pk], firing[node.pk], node.pk in silent
        )
        rows.append(
            NodeListRow(
                pk=node.pk,
                name=node_name(node.hostname),
                role="This hub" if node.instance_id == self_id else "Peer",
                tone=tone,
                label=label,
                counts=severity_counts(node.pk, open_by_node.get(node.pk, {})),
                last_seen=node.last_seen,
                url=reverse("console:node", args=[node.pk]),
            )
        )
    rows.sort(key=lambda row: (TONE_RANK.get(row.tone, len(TONE_RANK)), row.name.casefold()))
    empty = "" if rows else "no_match" if Node.objects.exists() else "none"
    return NodeList(
        rows=tuple(rows),
        groups=(chip_group("freshness", "Freshness", FRESHNESS_CHIPS, criteria, base, counts),),
        q=criteria.q,
        kept=criteria.kept(),
        policy=Action("Fleet policy", intent="view_policy"),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
