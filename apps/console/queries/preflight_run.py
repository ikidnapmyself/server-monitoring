"""Read model for one preflight run's page. Returns dataclasses, never querysets."""

from dataclasses import dataclass, replace
from datetime import datetime

from apps.alerts.describing import node_name
from apps.alerts.models import Node
from apps.checkers.models import PreflightRun
from apps.checkers.preflight_describing import describe_preflight_check, describe_preflight_run
from apps.console.queries.preflight import trigger_label
from config.describing import Action, Description, Fact

GROUPS = (("error", "Errors"), ("warn", "Warnings"), ("info", "Info"), ("ok", "Passed"))


@dataclass(frozen=True)
class CheckGroup:
    key: str
    label: str
    checks: tuple[Description, ...]


@dataclass(frozen=True)
class PreflightDetail:
    pk: int
    what: Description
    trigger: str
    node: Action | None
    counts: str
    created_at: datetime
    groups: tuple[CheckGroup, ...]


def _node(instance_id: str) -> Action | None:
    node = Node.objects.filter(instance_id=instance_id).exclude(instance_id="").first()
    if node is None:
        return None
    return Action(node_name(node.hostname), "view_node", {"pk": node.pk})


def _facts(run: PreflightRun) -> tuple[Fact, ...]:
    facts = [Fact("Status", run.overall_status)]
    if run.instance_id:
        facts.append(Fact("Instance ID", run.instance_id, copy=True))
    if run.triggered_by:
        facts.append(Fact("Triggered by", run.triggered_by))
    return tuple(facts)


def _plural(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else word + 's'}"


def build_preflight_detail(pk: int) -> PreflightDetail | None:
    run = PreflightRun.objects.filter(pk=pk).first()
    if run is None:
        return None
    what = describe_preflight_run(run)
    checks = list(run.checks.order_by("pk"))
    levels = {key for key, _ in GROUPS}
    groups = [
        CheckGroup(key, label, tuple(describe_preflight_check(c) for c in checks if c.level == key))
        for key, label in GROUPS
    ]
    odd = tuple(describe_preflight_check(c) for c in checks if c.level not in levels)
    if odd:
        groups.append(CheckGroup("other", "Other", odd))
    return PreflightDetail(
        pk=run.pk,
        what=replace(what, facts=(*what.facts, *_facts(run))),
        trigger=trigger_label(run.triggered_by),
        node=_node(run.instance_id),
        counts=(
            f"{run.passed} passed, {_plural(run.warnings, 'warning')}, "
            f"{_plural(run.errors, 'error')}"
        ),
        created_at=run.created_at,
        groups=tuple(group for group in groups if group.checks),
    )
