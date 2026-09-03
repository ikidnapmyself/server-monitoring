"""Every hub-side policy override on this hub, as one row per node and checker.

``build_effective_policy`` answers "what does this node's config actually do?"
for one node, in three lists. This module flattens that answer for printing. It
adds no policy rule of its own.

Design: docs/plans/2026-09-03-policy-overview-design.md
"""

from dataclasses import dataclass

from django.urls import reverse

from apps.alerts.models import Node
from apps.alerts.node_policy import (
    PolicySection,
    UnreadKey,
    build_effective_policy,
    field_name,
    spec_for,
)

# The change form's own panel headings, so one state does not get two names.
IN_EFFECT = "In effect"
NOT_SCORING = "Saved but not scoring"
NOT_HONOURED = "Not honoured"

# Worst first, because a checker can land in two of the three lists at once and
# the row shows one badge. "Saved but not scoring" outranks "Not honoured": a
# half-filled threshold pair is a decision an operator has to finish, while a
# leftover key changes no severity.
_WORST_FIRST = [NOT_SCORING, NOT_HONOURED, IN_EFFECT]

NO_POLICY = "—"


@dataclass(frozen=True)
class PolicyRow:
    """One checker's override on one node, ready to print."""

    checker: str
    policy: str
    status: str
    why: str
    node_url: str
    edit_url: str

    @property
    def is_problem(self) -> bool:
        """Whether this override is changing no severity."""
        return self.status != IN_EFFECT


def _node_url(node) -> str:
    """The admin change page for one node."""
    return reverse("admin:alerts_node_change", args=[node.pk])


def _edit_url(checker: str, node_url: str) -> str:
    """The node page, landing on this checker's own boxes where it has any.

    The anchor is the first box rather than the section heading because the
    admin's fieldset template carries no id of its own, while ``field_name``
    plus Django's ``id_`` prefix gives every policy input a stable one. A checker
    name reaches the fragment only after matching a ``FIELD_SPECS`` key, so a
    name off a webhook never lands in the URL.
    """
    spec = spec_for(checker)
    if not spec:
        return node_url
    return f"{node_url}#id_{field_name(checker, spec[0].name)}"


def _unread_sentences(entries: list[UnreadKey]) -> list[str]:
    """What the unread entries for one checker mean, in plain sentences.

    A blank ``key`` covers two different problems: no scorer knows the checker,
    or a known checker holds something that is not a mapping. "Nothing reads
    cpu" is false in the second case, so ``spec_for`` separates them.
    """
    labels = []
    sentences = []
    for entry in entries:
        if not entry.key and spec_for(entry.checker):
            sentences.append(
                f"{entry.checker} is set to something that is not a policy, so nothing reads it."
            )
        else:
            labels.append(entry.label)
    if labels:
        sentences.append(f"Nothing reads {', '.join(labels)}.")
    return sentences


def _why(section: PolicySection | None, unread: list[UnreadKey]) -> str:
    """Why this row is not simply working, in the panel's own sentences.

    A section never carries both an inactive reason and an editor note today.
    Both are asked for anyway, so a change in ``node_policy`` shows up as an odd
    sentence rather than a silently dropped one.
    """
    parts = []
    if section is not None and section.inactive_reason:
        parts.append(section.inactive_reason)
    if section is not None and section.editor_note:
        parts.append(f"Scoring as stored, but {section.editor_note}")
    parts.extend(_unread_sentences(unread))
    return " ".join(parts)


def rows_for_node(node) -> list[PolicyRow]:
    """One row per checker this node has any config entry for, sorted by checker."""
    policy = build_effective_policy(node)
    node_url = _node_url(node)
    unread: dict[str, list[UnreadKey]] = {}
    for entry in policy.unread:
        unread.setdefault(entry.checker, []).append(entry)
    sections = {section.checker: (section, IN_EFFECT) for section in policy.sections}
    sections.update({section.checker: (section, NOT_SCORING) for section in policy.inactive})
    rows = []
    for checker in sorted(set(sections) | set(unread)):
        section, status = sections.get(checker, (None, NOT_HONOURED))
        statuses = [status] + ([NOT_HONOURED] if checker in unread else [])
        rows.append(
            PolicyRow(
                checker=checker,
                policy=(
                    ", ".join(f"{value.label} {value.value}" for value in section.values)
                    if section is not None
                    else NO_POLICY
                ),
                status=min(statuses, key=_WORST_FIRST.index),
                why=_why(section, unread.get(checker, [])),
                node_url=node_url,
                edit_url=_edit_url(checker, node_url),
            )
        )
    return rows


@dataclass(frozen=True)
class NodeGroup:
    instance_id: str
    hostname: str
    node_url: str
    rows: list[PolicyRow]
    has_problem: bool


@dataclass(frozen=True)
class PolicyOverview:
    """Every node holding policy, plus a count of the ones that hold none."""

    groups: list[NodeGroup]
    quiet_count: int


def build_policy_overview() -> PolicyOverview:
    """Every hub-side override on this hub, the broken ones first.

    A node with no config is counted rather than listed, because a page of
    dashes would bury the rows worth reading.
    """
    groups, quiet_count = [], 0
    for node in Node.objects.order_by():
        rows = rows_for_node(node)
        if not rows:
            quiet_count += 1
            continue
        groups.append(
            NodeGroup(
                instance_id=node.instance_id,
                hostname=node.hostname,
                node_url=_node_url(node),
                rows=rows,
                has_problem=any(row.is_problem for row in rows),
            )
        )
    groups.sort(key=lambda group: (not group.has_problem, group.instance_id))
    return PolicyOverview(groups=groups, quiet_count=quiet_count)
