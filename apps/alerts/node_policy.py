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

from apps.alerts.reevaluation import PRIMARY_METRIC, _number


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


class PolicyError(ValueError):
    """A policy value an operator typed that the scorers could not use.

    The message is shown to that operator as a field error, so it says what to
    fix rather than that something is invalid.
    """


def clean_number(value) -> float:
    """A threshold the scorers will accept, as a float.

    Reuses ``reevaluation._number`` rather than restating its rule: the whole
    point of this module is that the editor rejects exactly what the runtime
    would silently ignore. A second copy of "bool is not a number" is a second
    thing to drift.
    """
    number = _number(value)
    if number is None:
        raise PolicyError("Enter a number.")
    return number


def clean_thresholds(warning, critical) -> tuple[float, float] | None:
    """Both thresholds, or ``None`` when the checker has no policy at all.

    ``_score_numeric`` returns ``None`` unless both thresholds are present and
    ``critical >= warning``, so a half-filled or inverted pair saves cleanly and
    then does nothing. Both blank is the one legitimate way to say "no policy",
    so that is the only case allowed through empty.
    """
    if warning is None and critical is None:
        return None
    if warning is None:
        raise PolicyError("Set a warning threshold too, or clear both.")
    if critical is None:
        raise PolicyError("Set a critical threshold too, or clear both.")
    warn = clean_number(warning)
    crit = clean_number(critical)
    if crit < warn:
        raise PolicyError("The critical threshold must not be below the warning threshold.")
    return warn, crit


# The scorers do not check port bounds: `_int_set` coerces any number. The editor
# is allowed to be stricter than the runtime, never looser, and a port outside
# 1-65535 can only ever be a typo.
_MIN_PORT = 1
_MAX_PORT = 65535


def clean_int_list(value: str) -> list[int]:
    """Parse a comma-separated port list; ``[]`` for blank input.

    An empty list is a real policy, not the absence of one: ``_score_allowlist``
    with an empty allowlist flags only externally-exposed ports.
    """
    ports: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            port = int(token)
        except ValueError:
            raise PolicyError(f"'{token}' is not a port number.") from None
        if not _MIN_PORT <= port <= _MAX_PORT:
            raise PolicyError(f"Port {port} is outside {_MIN_PORT}-{_MAX_PORT}.")
        ports.append(port)
    return ports
