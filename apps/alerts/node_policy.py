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


# The prefix keeps policy fields out of the way of the model form's own fields.
_PREFIX = "policy"


def field_name(checker: str, field: str) -> str:
    """The flat form field name for one policy key."""
    return f"{_PREFIX}__{checker}__{field}"


def _json_dict(value) -> dict:
    """A JSON field read as a dict, whatever it actually holds.

    ``Node.config`` is a ``JSONField`` and the ingest path never validates it, so
    it can hold a string, and so can any entry inside it. ``node_overview`` keeps
    its own copy of this two-liner: importing it here would pull the admin panels
    (and through them ``apps.alerts.models``) into a module the form and the
    scorers both sit next to, and Task 7 puts the policy form on the very page
    ``node_overview`` builds, which would close the loop into a real cycle.
    """
    return value if isinstance(value, dict) else {}


def to_form_values(config) -> dict:
    """The editable parts of a ``Node.config``, flattened for a form.

    Only keys that are actually present are emitted, so a checker with no policy
    leaves its fields blank rather than filled with an invented default. Values
    are handed back as they were stored: the round trip must not rewrite an
    untouched config.
    """
    values: dict = {}
    for checker, raw_entry in _json_dict(config).items():
        entry = _json_dict(raw_entry)
        for field in spec_for(checker):
            if field.name not in entry:
                continue
            value = entry[field.name]
            if field.kind == "int_list":
                value = ", ".join(str(port) for port in value) if isinstance(value, list) else ""
            values[field_name(checker, field.name)] = value
    return values


def to_config(values: dict, existing) -> dict:
    """Fold cleaned form values back into a config, keeping everything else.

    ``existing`` is the config as stored. Anything this module has no spec for —
    an unknown checker, an unknown key inside a known one — is carried across
    untouched, because a form that silently deletes what it cannot render is a
    form nobody can trust with a hand-written policy.

    A field the form did not submit is not an edit; ``None`` is, and clears the
    key. An emptied checker keeps its (now empty) entry: that is what tells the
    form the operator wants to see that section.
    """
    config = {
        checker: dict(entry) if isinstance(entry, dict) else entry
        for checker, entry in _json_dict(existing).items()
    }
    for checker, fields in FIELD_SPECS.items():
        for field in fields:
            key = field_name(checker, field.name)
            if key not in values:
                continue
            entry = config.get(checker)
            entry = dict(entry) if isinstance(entry, dict) else {}
            value = values[key]
            if value is None:
                entry.pop(field.name, None)
            else:
                if field.kind == "int_list":
                    value = clean_int_list(value)
                stored = entry.get(field.name)
                # A form field yields a float where the config held an int, so an
                # untouched save would otherwise rewrite 80 as 80.0 and read as a
                # policy change. Numerically equal means unchanged: keep what was
                # stored. This belongs here because this is the only place that
                # sees both the submitted value and the one it replaces.
                entry[field.name] = stored if stored == value else value
            config[checker] = entry
    return config
