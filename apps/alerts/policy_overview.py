"""One table of every hub-side policy override on this hub.

``build_effective_policy`` already answers "what does this node's config
actually do?" for one node, in three lists. This module is that answer for the
fleet, flattened to one row per node and checker so a policy that scores nothing
is visible without opening eight change pages in turn.

It adds no rule of its own. Every status, value and sentence here comes from
``node_policy``, because a second opinion about what a stored threshold does is
a second thing to drift.

Design: docs/plans/2026-09-03-policy-overview-design.md
"""

from dataclasses import dataclass

from django.urls import reverse

from apps.alerts.node_policy import build_effective_policy, field_name, spec_for

# The three ways a config entry ends up, in the words the page prints. They are
# the three lists ``EffectivePolicy`` returns, named once here so the template
# and the tests agree with the builder.
IN_EFFECT = "In effect"
NOT_SCORING = "Not scoring"
NOT_HONOURED = "Not honoured"

# What the Policy cell says for a checker with no value any scorer reads. A
# blank cell there reads as a rendering fault; this row is the whole point of
# the page, so it says so.
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


def _edit_url(checker: str, node_url: str) -> str:
    """The node page, landing on this checker's own boxes where it has any.

    ``NodePolicyForm`` builds its fields through ``field_name`` and Django
    prefixes the rendered input with ``id_``, so the input is a stable anchor.
    The admin's fieldset template carries no id of its own, which is why the
    anchor is the first box rather than the section heading.

    A checker with no spec has no boxes on that page at all, so it gets the page
    and no fragment rather than a link to nothing.
    """
    spec = spec_for(checker)
    if not spec:
        return node_url
    return f"{node_url}#id_{field_name(checker, spec[0].name)}"


def _why(section, ignored: list[str]) -> str:
    """Why this row is not simply working, in the panel's own sentences.

    At most one explanation comes from the section itself: a section that scores
    nothing already carries the reason, and ``build_effective_policy`` only sets
    ``editor_note`` on a section that scores. Ignored keys are additional, since
    a checker can score and still hold a key nobody reads.
    """
    parts = []
    if section is not None and section.inactive_reason:
        parts.append(section.inactive_reason)
    elif section is not None and section.editor_note:
        parts.append(f"Scoring as stored, but {section.editor_note}")
    if ignored:
        parts.append(f"Nothing reads {', '.join(ignored)}.")
    return " ".join(parts)


def rows_for_node(node) -> list[PolicyRow]:
    """One row per checker this node has any config entry for.

    A checker can appear in two of ``EffectivePolicy``'s lists at once: a
    threshold pair that scores plus a leftover key nothing reads. That is one
    decision an operator made, so it stays one row, and the worse of the two
    statuses wins. Splitting it would put "In effect" and "Not honoured" beside
    each other for the same checker and leave the reader to work out which half
    of the entry each referred to.
    """
    policy = build_effective_policy(node)
    node_url = reverse("admin:alerts_node_change", args=[node.pk])
    ignored: dict[str, list[str]] = {}
    for entry in policy.unread:
        ignored.setdefault(entry.checker, []).append(entry.label)
    sections = {section.checker: (section, IN_EFFECT) for section in policy.sections}
    sections.update({section.checker: (section, NOT_SCORING) for section in policy.inactive})
    rows = []
    for checker in sorted(set(sections) | set(ignored)):
        section, status = sections.get(checker, (None, NOT_HONOURED))
        rows.append(
            PolicyRow(
                checker=checker,
                policy=(
                    ", ".join(f"{value.label} {value.value}" for value in section.values)
                    if section is not None
                    else NO_POLICY
                ),
                # An ignored key is not honoured whatever else the entry does.
                status=NOT_HONOURED if checker in ignored else status,
                why=_why(section, ignored.get(checker, [])),
                node_url=node_url,
                edit_url=_edit_url(checker, node_url),
            )
        )
    return rows
