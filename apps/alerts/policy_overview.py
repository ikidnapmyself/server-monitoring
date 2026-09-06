"""Every hub-side policy override on this hub, as one row per node and checker.

``build_effective_policy`` answers "what does this node's config actually do?"
for one node, in three lists. This module flattens that answer for printing. It
adds no policy rule of its own.

A checker firing with no config entry behind it also gets a row, because a page
built from config alone cannot answer "is anything alerting that my policy does
not cover?".

Design: docs/plans/2026-09-03-policy-overview-design.md
"""

from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from django.urls import reverse

from apps.alerts.models import Alert, Node
from apps.alerts.node_policy import (
    PolicySection,
    UnreadKey,
    build_effective_policy,
    field_name,
    spec_for,
)
from apps.alerts.reevaluation import SCORERS

# The badge wording follows the change form's policy panel vocabulary, so one
# state does not get two names across the two surfaces.
IN_EFFECT = "In effect"
NOT_SCORING = "Saved but not scoring"
NOT_HONOURED = "Not honoured"

# The two states a firing alert can be in with no config entry behind it.
NO_POLICY_SET = "No policy set"
NOT_REEVALUATABLE = "Not re-evaluatable"

# Worst first, because a checker can land in two of the three config lists at
# once and the row shows one badge. "Saved but not scoring" outranks "Not
# honoured": a half-filled threshold pair is a decision an operator has to
# finish, while a leftover key changes no severity. "Not re-evaluatable" ranks
# below "In effect" because no edit an operator makes can move it.
_WORST_FIRST = [NOT_SCORING, NOT_HONOURED, NO_POLICY_SET, IN_EFFECT, NOT_REEVALUATABLE]

NO_POLICY = "—"


@dataclass(frozen=True)
class PolicyRow:
    """One checker's override on one node, ready to print."""

    checker: str
    policy: str
    status: str
    why: str
    caution: bool
    edit_url: str

    @property
    def is_problem(self) -> bool:
        """Whether this row is something an operator can and should close."""
        return self.status not in (IN_EFFECT, NOT_REEVALUATABLE)

    @property
    def is_muted(self) -> bool:
        """Whether this row states a ceiling rather than a fault."""
        return self.status == NOT_REEVALUATABLE


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
        parts.append(
            f"Scoring as stored, but {section.editor_note}"
            " Retyping it on the node page means changing it."
        )
    parts.extend(_unread_sentences(unread))
    return " ".join(parts)


def _firing_only_row(checker: str, node_url: str) -> PolicyRow:
    """The row for a checker this node is alerting on with no config entry.

    ``SCORERS`` decides which of the two states it is in, because that is the map
    re-evaluation actually dispatches on. A config key or a checker registration
    proves nothing about whether anything can score the checker.
    """
    if checker in SCORERS:
        return PolicyRow(
            checker=checker,
            policy=NO_POLICY,
            status=NO_POLICY_SET,
            why=f"Alerting now with no policy set, so {checker} scores as the node sends it.",
            caution=False,
            edit_url=_edit_url(checker, node_url),
        )
    return PolicyRow(
        checker=checker,
        policy=NO_POLICY,
        status=NOT_REEVALUATABLE,
        why=f"Alerting now, but no scorer reads {checker}, so no policy can change it.",
        caution=False,
        edit_url=node_url,
    )


def rows_for_node(node, firing: AbstractSet[str] = frozenset()) -> list[PolicyRow]:
    """One row per checker this node configures or is alerting on, sorted by checker.

    ``firing`` is the checkers with a firing alert on this node, gathered once by
    ``build_policy_overview`` so the page does not query per node. A checker in
    both places keeps its config-derived row: what the policy does outranks the
    fact that something is firing under it.
    """
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
                caution=section is not None and bool(section.editor_note),
                edit_url=_edit_url(checker, node_url),
            )
        )
    rows.extend(
        _firing_only_row(checker, node_url)
        for checker in sorted(set(firing) - set(sections) - set(unread))
    )
    # Only the un-actionable rows move: everything else stays alphabetical, which
    # is the order the node page's own policy panel prints.
    rows.sort(key=lambda row: (row.is_muted, row.checker))
    return rows


def _firing_checkers() -> dict[str, set[str]]:
    """The checkers with a firing alert, per node identity, in one query.

    Matched on the ``instance_id`` label and not the ``node`` FK, which is stamped
    only at alert creation: an alert raised before its node registered stays
    unlinked while still belonging to that node. ``ReevalScope._open`` matches the
    same way and for the same reason.
    """
    by_instance: dict[str, set[str]] = {}
    rows = (
        Alert.objects.filter(status="firing", labels__has_keys=["instance_id", "checker"])
        .order_by()
        .values_list("labels", flat=True)
    )
    for labels in rows:
        instance_id, checker = labels["instance_id"], labels["checker"]
        # labels is unvalidated JSON, so either value can be a list or a dict.
        if isinstance(instance_id, str) and isinstance(checker, str):
            by_instance.setdefault(instance_id, set()).add(checker)
    return by_instance


@dataclass(frozen=True)
class NodeGroup:
    """One node's rows, under the identity that names it."""

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
    """Every hub-side override on this hub, plus every gap in the cover, broken first.

    A node with neither config nor a firing alert is counted rather than listed,
    because a page of dashes would bury the rows worth reading.
    """
    groups, quiet_count = [], 0
    firing = _firing_checkers()
    # order_by() clears the model's own -last_seen ordering, because the groups
    # are sorted below on whether they hold a problem.
    for node in Node.objects.order_by():
        rows = rows_for_node(node, firing.get(node.instance_id, frozenset()))
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
