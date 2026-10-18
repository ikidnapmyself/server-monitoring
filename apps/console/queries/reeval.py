"""The re-evaluate preview every confirm page shares. Returns dataclasses, never querysets."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse

from apps.alerts.describing import (
    REOPEN_WARNING,
    describe_reeval_consequences,
    describe_reeval_report,
)
from apps.alerts.models import Node
from apps.alerts.reeval_existing import ReevalScope, preview_reeval, scope_digest
from apps.checkers.checkers import checker_label
from apps.console.queries.hosts import node_name
from config.describing import Description

NOTHING_OPEN = "No open alerts need re-evaluation."


@dataclass(frozen=True)
class ReevalPreview:
    title: str
    rows: tuple[Description, ...]
    notes: tuple[str, ...]
    warning: str
    empty: str
    can_apply: bool
    digest: str
    back_url: str
    back_text: str
    url: str


def reeval_preview(
    scope: ReevalScope,
    *,
    title: str,
    back_url: str,
    back_text: str,
    url: str,
    named: bool = False,
    empty: str = "",
) -> ReevalPreview:
    # Before the preview: if the scope moves in between, the apply refuses rather than
    # writing a change the operator never saw.
    digest = scope_digest(scope)
    report = preview_reeval(scope)
    rows = describe_reeval_report(report, named=named)
    return ReevalPreview(
        title=title,
        rows=rows,
        notes=describe_reeval_consequences(report),
        warning=REOPEN_WARNING if report.reopened_count else "",
        empty="" if rows else empty,
        can_apply=bool(report.changes) and report.node is not None,
        digest=digest,
        back_url=back_url,
        back_text=back_text,
        url=url,
    )


def node_scope(node: Node, checker: str) -> ReevalScope:
    """One checker when named, else the whole node.

    A named checker narrows even when it matches nothing: a stale link widened to the whole
    node would re-evaluate, and notify about, alerts nobody asked about.
    """
    return ReevalScope.for_checker(node, checker) if checker else ReevalScope.for_node(node)


def build_node_reeval_preview(pk: int, checker: str = "") -> ReevalPreview | None:
    node = Node.objects.filter(pk=pk).first()
    if node is None:
        return None
    name = node_name(node.hostname)
    url = reverse("console:node-reeval", args=[node.pk])
    if checker:
        label = checker_label(checker)
        title = f"Re-evaluate the {label} alerts on {name}"
        empty = f"No open {label} alerts on {name}."
        url += f"?{urlencode({'checker': checker})}"
    else:
        title, empty = f"Re-evaluate the open alerts on {name}", NOTHING_OPEN
    return reeval_preview(
        node_scope(node, checker),
        title=title,
        back_url=reverse("console:node", args=[node.pk]),
        back_text="Back to the node",
        url=url,
        named=True,
        empty=empty,
    )
