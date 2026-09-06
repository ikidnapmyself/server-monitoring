"""Every hub-side policy override on this hub, as one row per node and checker.

``build_effective_policy`` answers "what does this node's config actually do?"
for one node, in three lists. This module flattens that answer for printing. It
adds no policy rule of its own.

A checker firing with no config entry behind it also gets a row, because a page
built from config alone cannot answer "is anything alerting that my policy does
not cover?".

Each row also carries what the policy is doing to the alerts open right now and
when it last visibly did anything, so a rule that reads fine can still be shown
to be changing nothing.

Design: docs/plans/2026-09-03-policy-overview-design.md
"""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.formats import date_format
from django.utils.timezone import localtime

from apps.alerts.models import Alert, AlertHistory, Node
from apps.alerts.node_policy import (
    PolicySection,
    UnreadKey,
    build_effective_policy,
    field_name,
    spec_for,
)
from apps.alerts.reeval_existing import _outcome_for
from apps.alerts.reevaluation import SCORERS, Verdict

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

# The marker apply_reeval stamps on every AlertHistory row it writes. Filtering on
# it is what separates a policy re-evaluation from an ingest-driven resolve, which
# writes history under no marker at all.
POLICY_MARKER = "hub-node-policy:config-change"

# The ingest path writes this annotation only when it actually changed a severity,
# so its presence on a firing alert is evidence the policy is live. It carries no
# time of its own, which is why the row says so instead of inventing one.
INGEST_ANNOTATION = "severity_reevaluated"

NEVER_APPLIED = "Never"
APPLIED_AT_INGEST = "At ingest, time not recorded"

NOTHING_FIRING = "Nothing firing"


@dataclass(frozen=True)
class CheckerEvidence:
    """What one checker's policy is doing on one node, and when it last did anything."""

    firing_count: int = 0
    would_change_count: int = 0
    last_applied: datetime | None = None
    applied_at_ingest: bool = False


NO_EVIDENCE = CheckerEvidence()


@dataclass(frozen=True)
class PolicyRow:
    """One checker's override on one node, ready to print."""

    checker: str
    policy: str
    status: str
    why: str
    caution: bool
    edit_url: str
    firing_count: int = 0
    would_change_count: int = 0
    last_applied: datetime | None = None
    applied_at_ingest: bool = False
    reeval_url: str = ""

    @property
    def effect(self) -> str:
        """What this policy would do to the alerts open right now.

        Scored with the same function the confirm preview uses, so the page and
        the preview cannot disagree about what would change.
        """
        if not self.firing_count:
            return NOTHING_FIRING
        if not self.would_change_count:
            return f"{self.firing_count} firing, none would change"
        return f"{self.firing_count} firing, {self.would_change_count} would change"

    @property
    def applied(self) -> str:
        """When a re-evaluation last visibly changed something for this checker.

        This tells a policy re-evaluation from an ingest resolve, not from a human
        one: ``AlertAdmin.resolve_selected`` writes no history at all.
        """
        if self.last_applied is not None:
            return date_format(localtime(self.last_applied), "DATETIME_FORMAT")
        if self.applied_at_ingest:
            return APPLIED_AT_INGEST
        return NEVER_APPLIED

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


def _reeval_url(node, checker: str) -> str:
    """The node's existing re-evaluate action, narrowed to one checker.

    A query parameter rather than a second action, so both buttons land on the one
    confirm-then-apply flow. The checker arrives over a webhook, so it is
    URL-encoded here and the action validates it before scoping anything.
    """
    tool_url = reverse(
        "admin:alerts_node_actions", kwargs={"pk": node.pk, "tool": "reevaluate_open_alerts"}
    )
    return f"{tool_url}?{urlencode({'checker': checker})}"


def _with_evidence(row: PolicyRow, node, evidence: CheckerEvidence) -> PolicyRow:
    """Attach what this row's policy is doing, and a button where it can do something.

    A row with nothing firing gets no button because the action only ever scores
    firing alerts, and a muted row gets none for the same reason it gets no Edit
    link: no policy can move it.
    """
    return replace(
        row,
        firing_count=evidence.firing_count,
        would_change_count=evidence.would_change_count,
        last_applied=evidence.last_applied,
        applied_at_ingest=evidence.applied_at_ingest,
        reeval_url=(
            _reeval_url(node, row.checker) if evidence.firing_count and not row.is_muted else ""
        ),
    )


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


def rows_for_node(
    node,
    firing: AbstractSet[str] = frozenset(),
    evidence: Mapping[str, CheckerEvidence] | None = None,
) -> list[PolicyRow]:
    """One row per checker this node configures or is alerting on, sorted by checker.

    ``firing`` is the checkers with a firing alert on this node and ``evidence`` is
    what those alerts and this node's history say about each checker, both gathered
    once by ``build_policy_overview`` so the page does not query per node. A checker
    in both places keeps its config-derived row: what the policy does outranks the
    fact that something is firing under it.
    """
    evidence = evidence or {}
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
    rows = [_with_evidence(row, node, evidence.get(row.checker, NO_EVIDENCE)) for row in rows]
    # Only the un-actionable rows move: everything else stays alphabetical, which
    # is the order the node page's own policy panel prints.
    rows.sort(key=lambda row: (row.is_muted, row.checker))
    return rows


def _named(labels) -> tuple[str, str] | None:
    """The node identity and checker a row belongs to, or None if either is not a name.

    ``labels`` is unvalidated JSON off a webhook, so either value can be a list or
    a dict rather than a name.
    """
    instance_id, checker = labels["instance_id"], labels["checker"]
    if isinstance(instance_id, str) and isinstance(checker, str):
        return instance_id, checker
    return None


def _firing_alerts() -> dict[str, dict[str, list[Alert]]]:
    """Every firing alert, per node identity and checker, in one query.

    Whole alerts and not just their labels, because the rows score them: gathering
    them once here is what keeps the page off one query per row.

    Matched on the ``instance_id`` label and not the ``node`` FK, which is stamped
    only at alert creation: an alert raised before its node registered stays
    unlinked while still belonging to that node. ``ReevalScope._open`` matches the
    same way and for the same reason.
    """
    by_instance: dict[str, dict[str, list[Alert]]] = {}
    alerts = Alert.objects.filter(
        status="firing", labels__has_keys=["instance_id", "checker"]
    ).order_by()
    for alert in alerts:
        named = _named(alert.labels)
        if named is None:
            continue
        instance_id, checker = named
        by_instance.setdefault(instance_id, {}).setdefault(checker, []).append(alert)
    return by_instance


def _applied_at() -> dict[str, dict[str, datetime]]:
    """When a policy re-evaluation last wrote history, per node identity and checker.

    One query, filtered on the marker ``apply_reeval`` stamps, so an ingest-driven
    resolve does not read as a policy that is doing something.
    """
    by_instance: dict[str, dict[str, datetime]] = {}
    rows = (
        AlertHistory.objects.filter(
            details__by=POLICY_MARKER,
            alert__labels__has_keys=["instance_id", "checker"],
        )
        .order_by()
        .values_list("alert__labels", "created_at")
    )
    for labels, created_at in rows:
        named = _named(labels)
        if named is None:
            continue
        instance_id, checker = named
        seen = by_instance.setdefault(instance_id, {})
        if checker not in seen or created_at > seen[checker]:
            seen[checker] = created_at
    return by_instance


def _evidence_for(
    node, alerts: dict[str, list[Alert]], applied: dict[str, datetime]
) -> dict[str, CheckerEvidence]:
    """What each of this node's checkers is doing, scored in memory.

    ``_outcome_for`` is the confirm preview's own scorer and runs no queries, so
    the page cannot promise a different number from the one the preview shows.
    """
    config = node.config or {}
    evidence = {
        checker: CheckerEvidence(
            firing_count=len(firing),
            would_change_count=sum(
                1 for alert in firing if isinstance(_outcome_for(alert, config), Verdict)
            ),
            last_applied=applied.get(checker),
            applied_at_ingest=any(
                INGEST_ANNOTATION in (alert.annotations or {}) for alert in firing
            ),
        )
        for checker, firing in alerts.items()
    }
    # A checker that has been re-evaluated into silence has history and nothing
    # firing, which is exactly the row an operator needs to read as "it worked".
    for checker, moment in applied.items():
        if checker not in evidence:
            evidence[checker] = CheckerEvidence(last_applied=moment)
    return evidence


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
    firing = _firing_alerts()
    applied = _applied_at()
    # order_by() clears the model's own -last_seen ordering, because the groups
    # are sorted below on whether they hold a problem.
    for node in Node.objects.order_by():
        node_alerts = firing.get(node.instance_id, {})
        evidence = _evidence_for(node, node_alerts, applied.get(node.instance_id, {}))
        rows = rows_for_node(node, set(node_alerts), evidence)
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
