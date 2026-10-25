"""Read model for one check run's page. Returns dataclasses, never querysets."""

import json
from dataclasses import dataclass, replace
from datetime import datetime

from apps.alerts.describing import node_name
from apps.alerts.models import Node
from apps.checkers.checkers import checker_label
from apps.checkers.models import CheckRun
from apps.console.queries.checks import describe_check_run, took
from apps.orchestration.models import PipelineRun
from config.describing import Action, Description, Fact


@dataclass(frozen=True)
class CheckDetail:
    pk: int
    what: Description
    checker: str
    host: str
    node: Action | None
    thresholds: str
    alert: Action | None
    run: Action | None
    executed_at: datetime
    took: str
    raw: str


def _number(value: float) -> str:
    return f"{value:f}".rstrip("0").rstrip(".")


def thresholds(warning: float | None, critical: float | None) -> str:
    parts = []
    if warning is not None:
        parts.append(f"warns at {_number(warning)}")
    if critical is not None:
        parts.append(f"critical at {_number(critical)}")
    return ", ".join(parts).capitalize()


def _node(hostname: str) -> Action | None:
    """The node when exactly one has this hostname. A check run stores no node key."""
    found = list(Node.objects.filter(hostname=hostname).exclude(hostname="")[:2])
    if len(found) != 1:
        return None
    return Action(node_name(found[0].hostname), "view_node", {"pk": found[0].pk})


def _run(trace_id: str) -> Action | None:
    """A rerun with nothing new mints a trace but no run, so a trace alone proves nothing."""
    if not trace_id or not PipelineRun.objects.filter(trace_id=trace_id).exists():
        return None
    return Action("Part of a run", "view_runs", {"q": trace_id})


def _facts(run: CheckRun) -> tuple[Fact, ...]:
    facts = [Fact("Status", run.status)]
    if run.error:
        facts.append(Fact("Error", run.error))
    if run.trace_id:
        facts.append(Fact("Trace ID", run.trace_id, copy=True))
    return tuple(facts)


def build_check_detail(pk: int) -> CheckDetail | None:
    run = CheckRun.objects.filter(pk=pk).first()
    if run is None:
        return None
    what = describe_check_run(run)
    return CheckDetail(
        pk=run.pk,
        what=replace(what, facts=(*what.facts, *_facts(run))),
        checker=checker_label(run.checker_name),
        host=run.hostname,
        node=_node(run.hostname),
        thresholds=thresholds(run.warning_threshold, run.critical_threshold),
        alert=(
            Action(f"Alert #{run.alert_id}", "view_alert", {"pk": run.alert_id})
            if run.alert_id
            else None
        ),
        run=_run(run.trace_id),
        executed_at=run.executed_at,
        took=took(run.duration_ms),
        raw=json.dumps(run.metrics, indent=2, sort_keys=True, default=str),
    )
