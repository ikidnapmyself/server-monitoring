"""The editable shape of ``Node.config``, derived from the scorers that read it.

``apps.alerts.reevaluation`` is deliberately fail-open: it runs in the ingest
path and returns ``None`` (passthrough) for any policy it cannot use, so a
malformed threshold is silently indistinguishable from no policy at all. This
module is the other half of that bargain: the editor, where a human is typing
and can simply be told they are wrong.

The spec is derived from ``SCORERS`` rather than restated, so adding a scorer
adds a form section with no edit here. A scorer added without a matching spec is
caught by ``apps/alerts/_tests/test_node_policy.py``, matching how
``config.SECTION_MAP`` completeness is guarded.
"""

from dataclasses import dataclass

from apps.alerts.reevaluation import PRIMARY_METRIC


@dataclass(frozen=True)
class PolicyField:
    """One editable key inside a checker's ``Node.config`` entry."""

    name: str
    kind: str  # "number" | "int_list"
    label: str
    help_text: str


_NUMERIC_FIELDS = [
    PolicyField(
        name="warning_threshold",
        kind="number",
        label="Warning at",
        help_text="Raise a warning at or above this value.",
    ),
    PolicyField(
        name="critical_threshold",
        kind="number",
        label="Critical at",
        help_text="Raise a critical at or above this value. Must not be below the warning.",
    ),
]

_ALLOWLIST_FIELDS = [
    PolicyField(
        name="allowlist",
        kind="int_list",
        label="Allowed ports",
        help_text="Comma-separated port numbers. Any listening port not listed is flagged.",
    ),
]

FIELD_SPECS: dict[str, list[PolicyField]] = {
    **{checker: _NUMERIC_FIELDS for checker in PRIMARY_METRIC},
    "listening_ports": _ALLOWLIST_FIELDS,
}


def spec_for(checker: str) -> list[PolicyField]:
    """The editable fields for one checker; empty for a checker with no policy."""
    return FIELD_SPECS.get(checker, [])
