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
from apps.console.queries.alert import IncidentCard, incident_cards
from apps.console.queries.hosts import node_keys, node_name
from apps.console.queries.nodes import SeverityCount, open_incidents_by_node, severity_counts
from apps.console.queries.overview import SEVERITY_RANK, firing_by_node, node_badge
from apps.console.queries.runs import RunListRow, recent_runs
from config.describing import Action, Description, Fact, or_action

RUN_LIMIT = 10


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
    value: str
    observed_at: object
    action: Action


@dataclass(frozen=True)
class PolicyLine:
    key: str
    label: str
    values: tuple[str, ...]
    note: str
    edit: Action | None


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


def _policy(
    node: Node, can_change: bool
) -> tuple[tuple[PolicyLine, ...], tuple[PolicyLine, ...], tuple[Fact, ...]]:
    policy = build_effective_policy(node)

    def line(section: PolicySection, note: str) -> PolicyLine:
        edit = Action("Edit", "edit_node_policy", {"pk": node.pk, "checker": section.checker})
        return PolicyLine(
            key=section.checker,
            label=section.title,
            values=tuple(f"{value.label} {value.value}" for value in section.values),
            note=note,
            edit=edit if can_change else None,
        )

    return (
        tuple(line(section, section.editor_note) for section in policy.sections),
        tuple(line(section, section.inactive_reason) for section in policy.inactive),
        tuple(Fact(checker_label(entry.checker), entry.label) for entry in policy.unread),
    )


def build_node_detail(pk: int, *, can_change: bool = False) -> NodeDetail | None:
    """One node for its page. ``do`` and the policy edit links hold only what the caller may do."""
    node = Node.objects.filter(pk=pk).first()
    if node is None:
        return None
    identity = build_identity(node)
    firing, critical, warning = firing_by_node()
    tone, label = node_badge(
        critical[node.pk], warning[node.pk], firing[node.pk], identity.freshness_status == "warn"
    )
    by_severity = open_incidents_by_node([node]).get(node.pk, {})
    incidents = list(
        Incident.objects.filter(pk__in=set().union(*by_severity.values()))
        .annotate(rank=SEVERITY_RANK)
        .order_by("rank", "created_at")
    )
    preflight = build_preflight(node, identity)
    run = preflight.run
    in_effect, not_scoring, unread = _policy(node, can_change)
    do = (
        (
            Action("Edit policy", "edit_node_policy", {"pk": node.pk}),
            Action("Re-evaluate open alerts", "reevaluate_node", {"pk": node.pk}),
        )
        if can_change
        else ()
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
            CheckerLine(
                key=row.checker,
                label=checker_label(row.checker),
                status=row.status,
                value=row.value,
                observed_at=row.observed_at,
                action=row.action,
            )
            for row in build_checker_rows(node, identity)
        ),
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
        url=reverse("console:node", args=[node.pk]),
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
