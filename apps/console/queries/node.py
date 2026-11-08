"""Read model for one node's page. Returns dataclasses, never querysets."""

import json
from dataclasses import dataclass
from datetime import datetime

from django import forms
from django.urls import reverse

from apps.alerts.describing import alert_link, describe_alert
from apps.alerts.forms import ADD_SECTION_FIELD, NodePolicyForm
from apps.alerts.models import Incident, Node
from apps.alerts.node_overview import (
    Chart,
    Identity,
    build_charts,
    build_checker_rows,
    build_identity,
    build_preflight,
    charts_note,
    checker_time_label,
)
from apps.alerts.node_policy import (
    PolicySection,
    build_effective_policy,
    config_digest,
    editor_anchor,
)
from apps.alerts.reeval_existing import ReevalScope
from apps.checkers.checkers import checker_label
from apps.checkers.models import CheckStatus
from apps.console.filters import CHECK_STATUS_CHOICES
from apps.console.queries.alert import IncidentCard, incident_cards
from apps.console.queries.hosts import node_keys, node_name
from apps.console.queries.incidents import Chip
from apps.console.queries.nodes import SeverityCount, open_incidents_by_node, severity_counts
from apps.console.queries.overview import SEVERITY_RANK, firing_by_node, node_badge
from apps.console.queries.runs import RunListRow, recent_runs
from config.describing import Action, Description, Fact, or_action
from config.tones import CHECK_STATUS_TONES, tone_for

RUN_LIMIT = 10

CHECKER_FILTERS = {
    "problems": frozenset(CHECK_STATUS_CHOICES["failing"]),
    "ok": frozenset({"ok"}),
}
CHECKER_CHIPS = (("all", "All"), ("problems", "Problems"), ("ok", "OK"))
# Anything unranked, skipped included, sorts after ok: it isn't a reading at all.
CHECKER_RANK = {"critical": 0, "warning": 1, "unknown": 2, "ok": 3}
STATUS_LABELS = dict(CheckStatus.choices)


@dataclass(frozen=True)
class EditorGroup:
    label: str
    anchor: str
    fields: tuple[forms.BoundField, ...]
    alerts: tuple[Description, ...] = ()


@dataclass(frozen=True)
class PolicyEditor:
    pk: int
    name: str
    form: NodePolicyForm
    groups: tuple[EditorGroup, ...]
    add: forms.BoundField | None
    digest: str
    node_url: str
    url: str


@dataclass(frozen=True)
class CheckerLine:
    key: str
    label: str
    status: str
    status_label: str
    tone: str
    value: str
    observed_at: object
    action: Action


@dataclass(frozen=True)
class PolicyLine:
    key: str
    label: str
    values: tuple[str, ...]
    note: str
    edit: Action


@dataclass(frozen=True)
class NodeDetail:
    pk: int
    name: str
    role: str
    freshness: str
    freshness_tone: str
    tone: str
    label: str
    counts: tuple[SeverityCount, ...]
    checkers: tuple[CheckerLine, ...]
    checker_chips: tuple[Chip, ...]
    checker_time_label: str
    incidents: tuple[IncidentCard, ...]
    charts: tuple[Chart, ...]
    charts_note: str
    preflight: str
    preflight_at: datetime | None
    preflight_action: Action | None
    preflight_note: str
    runs: tuple[RunListRow, ...]
    all_runs: Action
    in_effect: tuple[PolicyLine, ...]
    not_scoring: tuple[PolicyLine, ...]
    unread: tuple[Fact, ...]
    do: tuple[Action, ...]
    facts: tuple[Fact, ...]
    raw: str
    url: str


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"


def _policy(node: Node) -> tuple[tuple[PolicyLine, ...], tuple[PolicyLine, ...], tuple[Fact, ...]]:
    policy = build_effective_policy(node)

    def line(section: PolicySection, note: str) -> PolicyLine:
        return PolicyLine(
            key=section.checker,
            label=section.title,
            values=tuple(f"{value.label} {value.value}" for value in section.values),
            note=note,
            edit=Action("Edit", "edit_node_policy", {"pk": node.pk, "checker": section.checker}),
        )

    return (
        tuple(line(section, section.editor_note) for section in policy.sections),
        tuple(line(section, section.inactive_reason) for section in policy.inactive),
        tuple(Fact(checker_label(entry.checker), entry.label) for entry in policy.unread),
    )


def _checker_lines(node: Node, identity: Identity) -> list[CheckerLine]:
    lines = [
        CheckerLine(
            key=row.checker,
            label=checker_label(row.checker),
            status=row.status,
            status_label=STATUS_LABELS.get(row.status, row.status.replace("_", " ").capitalize()),
            tone=tone_for(CHECK_STATUS_TONES, row.status),
            value=row.value,
            observed_at=row.observed_at,
            action=row.action,
        )
        for row in build_checker_rows(node, identity)
    ]
    return sorted(
        lines, key=lambda line: (CHECKER_RANK.get(line.status, len(CHECKER_RANK)), line.label)
    )


def _checker_url(base: str, key: str) -> str:
    return base if key == "all" else f"{base}?checkers={key}"


def _checker_chips(lines: list[CheckerLine], current: str, base: str) -> tuple[Chip, ...]:
    if not lines:
        return ()
    return tuple(
        Chip(
            key=key,
            label=label,
            url=_checker_url(base, key),
            active=key == current,
            count=sum(1 for line in lines if key == "all" or line.status in CHECKER_FILTERS[key]),
        )
        for key, label in CHECKER_CHIPS
    )


def build_node_detail(pk: int, checkers: str = "") -> NodeDetail | None:
    """One node for its page. ``checkers`` is the checker table's filter, All when unknown."""
    node = Node.objects.filter(pk=pk).first()
    if node is None:
        return None
    identity = build_identity(node)
    current = checkers if checkers in CHECKER_FILTERS else "all"
    base = reverse("console:node", args=[node.pk])
    lines = _checker_lines(node, identity)
    firing, critical, warning = firing_by_node()
    tone, label = node_badge(
        critical[node.pk], warning[node.pk], firing[node.pk], identity.freshness_status == "warn"
    )
    by_severity = open_incidents_by_node([node]).get(node.pk, {})
    incidents = list(
        Incident.objects.filter(pk__in=set().union(*by_severity.values()))
        .annotate(rank=SEVERITY_RANK)
        .order_by("rank", "opened_at")
    )
    preflight = build_preflight(node, identity)
    run = preflight.run
    in_effect, not_scoring, unread = _policy(node)
    do = (
        Action("Edit policy", "edit_node_policy", {"pk": node.pk}),
        Action("Re-evaluate open alerts", "reevaluate_node", {"pk": node.pk}),
    )
    facts = tuple(
        Fact(label, value, copy=label == "Instance ID")
        for label, value in (
            ("Instance ID", node.instance_id),
            ("Address", node.address),
            ("Last source", node.last_source),
        )
        if value
    )
    return NodeDetail(
        pk=node.pk,
        name=node_name(node.hostname),
        role=identity.role_label,
        freshness=(
            identity.freshness_label if identity.is_local else f"seen {identity.freshness_label}"
        ),
        freshness_tone=identity.freshness_tone,
        tone=tone,
        label=label,
        counts=severity_counts(node.pk, by_severity),
        checkers=tuple(
            line for line in lines if current == "all" or line.status in CHECKER_FILTERS[current]
        ),
        checker_chips=_checker_chips(lines, current, base),
        checker_time_label=checker_time_label(node, identity),
        incidents=incident_cards(incidents),
        charts=tuple(build_charts(node, identity)),
        charts_note=charts_note(node, identity),
        preflight=(
            f"{run.passed} passed, {_plural(run.warnings, 'warning')}, "
            f"{_plural(run.errors, 'error')}."
            if run
            else ""
        ),
        preflight_at=run.created_at if run else None,
        preflight_action=(
            Action("Open the preflight run", "view_preflight_run", {"pk": run.pk}) if run else None
        ),
        preflight_note=preflight.note,
        runs=recent_runs(node.pk, node_keys(node), RUN_LIMIT),
        all_runs=Action("All runs on this node", "view_runs", {"node": node.pk}),
        in_effect=in_effect,
        not_scoring=not_scoring,
        unread=unread,
        do=do,
        facts=facts,
        raw=json.dumps(
            {"labels": node.labels, "config": node.config}, indent=2, sort_keys=True, default=str
        ),
        url=_checker_url(base, current),
    )


def _open_alerts(node: Node, checker: str) -> tuple[Description, ...]:
    alerts = ReevalScope.for_checker(node, checker).alerts.order_by("pk")
    return tuple(or_action(describe_alert(alert), alert_link(alert.pk)) for alert in alerts)


def build_policy_editor(pk: int, data=None, *, digest: str | None = None) -> PolicyEditor | None:
    """The editor for one node. ``digest`` keeps the one an invalid submit carried."""
    node = Node.objects.filter(pk=pk).first()
    if node is None:
        return None
    form = NodePolicyForm(data, instance=node)
    return PolicyEditor(
        pk=node.pk,
        name=node_name(node.hostname),
        form=form,
        groups=tuple(
            EditorGroup(
                checker_label(checker),
                editor_anchor(checker),
                tuple(fields),
                _open_alerts(node, checker),
            )
            for checker, fields in form.policy_groups()
        ),
        add=form[ADD_SECTION_FIELD] if ADD_SECTION_FIELD in form.fields else None,
        digest=config_digest(node.config) if digest is None else digest,
        node_url=reverse("console:node", args=[node.pk]),
        url=reverse("console:node-policy", args=[node.pk]),
    )
