"""Read model for the fleet policy page. Returns dataclasses, never querysets."""

from dataclasses import dataclass

from apps.alerts.policy_overview import PolicyRow, build_policy_overview
from apps.checkers.checkers import checker_label
from apps.console.queries.hosts import node_name
from config.describing import Action, Fact


@dataclass(frozen=True)
class PolicyTableRow:
    key: str
    checker: str
    policy: str
    status: str
    tone: str
    why: str
    effect: str
    applied: str
    caution: bool
    edit: Action | None
    reeval: Action | None
    unread: tuple[Fact, ...]


@dataclass(frozen=True)
class PolicyGroup:
    pk: int
    name: str
    has_problem: bool
    view: Action
    rows: tuple[PolicyTableRow, ...]
    facts: tuple[Fact, ...]


@dataclass(frozen=True)
class PolicyPage:
    groups: tuple[PolicyGroup, ...]
    quiet_count: int


def _row(row: PolicyRow) -> PolicyTableRow:
    return PolicyTableRow(
        key=row.checker,
        checker=checker_label(row.checker),
        policy=row.policy,
        status=row.status,
        tone=row.tone,
        why=row.why,
        effect=row.effect,
        applied=row.applied,
        caution=row.caution,
        edit=row.edit,
        reeval=row.reeval,
        unread=tuple(Fact("Stored key", label) for label in row.unread),
    )


def build_policy_page() -> PolicyPage:
    overview = build_policy_overview()
    return PolicyPage(
        groups=tuple(
            PolicyGroup(
                pk=group.pk,
                name=node_name(group.hostname),
                has_problem=group.has_problem,
                view=Action("Open the node", "view_node", {"pk": group.pk}),
                rows=tuple(_row(row) for row in group.rows),
                facts=(Fact("Instance ID", group.instance_id, copy=True),),
            )
            for group in overview.groups
        ),
        quiet_count=overview.quiet_count,
    )
