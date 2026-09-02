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

from apps.alerts.identity import local_hostname, local_instance_id
from apps.alerts.reevaluation import PRIMARY_METRIC, _number
from apps.checkers.models import CheckRun


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


def _scoring_view(config) -> dict:
    """Just the keys a scorer would read, for comparing one config to another.

    ``to_config`` carries across every key it has no spec for, so a raw ``!=``
    on two configs answers "did anything change", not "did anything that scores
    change". A checker with nothing readable left in it is dropped entirely, so
    opening an empty section (``{"cpu": {}}``) compares equal to not having one:
    the marker is inert at runtime, and a change that alters no severity is not
    a change worth previewing.
    """
    view: dict = {}
    for checker, raw_entry in _json_dict(config).items():
        entry = _json_dict(raw_entry)
        keys = {field.name: entry[field.name] for field in spec_for(checker) if field.name in entry}
        if keys:
            view[checker] = keys
    return view


def scoring_changed(before, after) -> bool:
    """Whether the change from one config to another can re-score an alert.

    The question the admin asks to decide whether a save is worth previewing.
    Numeric equality is what makes an untouched save compare equal: ``to_config``
    keeps the stored ``80`` rather than the form's ``80.0``, and ``80 == 80.0``
    here as well, so a save that retypes the same number is not a change.
    """
    return _scoring_view(before) != _scoring_view(after)


def _reported_checkers(node) -> list:
    """The checkers this node currently reports, from whichever source it has.

    Two sources because ``CheckRun`` is never pushed to a hub: the machine this
    hub runs on has its own rows, a peer has only the alerts it pushed.

    ``node_overview.build_checker_rows`` answers the same question and is
    deliberately not reused. Importing it here would point ``node_policy`` at a
    module that pulls in the admin URLs, the sparkline renderer and
    ``config.dashboard``, and Task 6 puts this form on the page
    ``node_overview`` builds, which closes the loop into a real import cycle.
    The same reasoning already kept ``_json_dict`` duplicated above. What is
    needed here is also far less than that function returns: names only, so one
    ``distinct`` on each side rather than a newest-row query per checker.
    """
    if node.instance_id == local_instance_id():
        # Node.upsert only writes hostname when truthy, so the local row can
        # carry the blank default while its CheckRun rows are keyed by the real
        # machine name. order_by() clears the model ordering: left on, the sort
        # column joins the SELECT and nothing comes back distinct.
        return list(
            CheckRun.objects.filter(hostname=node.hostname or local_hostname())
            .order_by()
            .values_list("checker_name", flat=True)
            .distinct()
        )
    # Filtered in the database: checker alerts are bounded by fingerprint dedup,
    # a node's webhook alerts are not. A label that is missing, blank or not even
    # a string simply fails to match a spec name below, so it needs no guard.
    return [
        _json_dict(labels).get("checker")
        for labels in node.alerts.filter(labels__has_key="checker").values_list("labels", flat=True)
    ]


def sections_for(node) -> list[str]:
    """The checkers this node's policy form should show, sorted.

    The union of what the node reports and what its config already names, kept
    to what actually accepts policy. Offering a disk threshold on a machine that
    never reports disk is noise, but a checker that has gone quiet with a policy
    already saved must still show, or that policy becomes invisible and nobody
    can edit it back out.

    Driven from ``FIELD_SPECS`` rather than from the node's data, so a stale key
    for a checker that no longer exists cannot become an editable section.
    """
    reported = _reported_checkers(node)
    configured = list(_json_dict(node.config))
    return [
        checker for checker in sorted(FIELD_SPECS) if checker in reported or checker in configured
    ]


def addable_checkers(sections: list[str]) -> list[str]:
    """The checkers a node could be given a policy section for, sorted.

    Takes the sections rather than the node so the form and the admin can each
    ask this without a second trip through ``sections_for``'s queries, and so
    the two cannot disagree about which select to render.
    """
    return [checker for checker in sorted(FIELD_SPECS) if checker not in sections]


@dataclass(frozen=True)
class PolicyValue:
    """One key a scorer reads, ready to print."""

    label: str
    value: str


@dataclass(frozen=True)
class PolicySection:
    """One checker's policy, as it stands in ``Node.config`` right now.

    ``inactive_reason`` is blank for a policy the scorers can actually use, and
    otherwise says what is wrong with it in the same words the form would put on
    the box. A section with a reason is real config with the right keys that
    still scores nothing, which is neither "in effect" nor "not honoured".
    """

    checker: str
    title: str
    values: list[PolicyValue]
    inactive_reason: str = ""


@dataclass(frozen=True)
class UnreadKey:
    """A config entry no scorer reads. ``key`` is blank for a whole checker.

    ``label`` keeps the raw spelling of what is stored, underscores and all,
    where a section title is prettified. An operator has to find this key in the
    JSON to remove it, so the panel must print the key, not a nicer word for it.
    """

    checker: str
    key: str
    label: str


@dataclass(frozen=True)
class EffectivePolicy:
    """Three lists, because there are three ways a config key can end up.

    ``sections`` scores. ``inactive`` holds sections the scorers skip whole:
    right keys, unusable values. ``unread`` holds keys no scorer looks at.
    """

    sections: list[PolicySection]
    inactive: list[PolicySection]
    unread: list[UnreadKey]

    @property
    def has_content(self) -> bool:
        """Whether there is any policy at all, honoured or not."""
        return bool(self.sections or self.inactive or self.unread)


def _format_policy_value(field: PolicyField, value) -> str:
    """One stored value as a human reads it.

    ``Node.config`` is unvalidated JSON, so a port list can be anything. A value
    that is not the shape the field expects is printed as it was stored: this
    panel reports what is there, and inventing a dash for it would hide exactly
    the sort of hand-written mistake it exists to show.
    """
    if field.kind == "int_list" and isinstance(value, list):
        return ", ".join(str(port) for port in value)
    return str(value)


def _inactive_reason(checker: str, entry: dict) -> str:
    """Why the scorers skip this entry whole, or ``""`` when they read it.

    Asked of ``clean_thresholds`` rather than restated, so the panel judges a
    stored policy by exactly the rule the editor enforces and ``_score_numeric``
    applies: both thresholds, both numbers, critical not below warning. A pair
    failing any of those returns ``None`` from the scorer, which is passthrough,
    so calling it "in effect" is the same lie the empty-entry rule already
    refuses to tell. The message is the one the form puts on the box, so an
    operator reads the same sentence wherever they meet the problem.

    Only numeric checkers have a whole-entry precondition worth stating here; an
    allowlist is judged key by key by ``_score_allowlist``.
    """
    if checker not in PRIMARY_METRIC:
        return ""
    try:
        clean_thresholds(entry.get("warning_threshold"), entry.get("critical_threshold"))
    except PolicyError as exc:
        return str(exc)
    return ""


def build_effective_policy(node) -> EffectivePolicy:
    """What this node's config actually does, and what it merely holds.

    Three lists because ``to_config`` preserves every key it has no spec for, so
    nothing an operator wrote is silently deleted. The price is that a key left
    behind by a checker that no longer exists looks identical to a live one, and
    an operator with view-but-not-change permission sees no policy at all: the
    admin renders every fieldset field read-only and cannot resolve the form's
    dynamically-added names. This panel is the read-only answer to both.

    An empty entry is left out of every list. ``{"cpu": {}}`` is the marker that
    opens a section (see ``NodePolicyForm.clean``), it scores nothing, and there
    is no key in it for anything to ignore.
    """
    sections: list[PolicySection] = []
    inactive: list[PolicySection] = []
    unread: list[UnreadKey] = []
    for checker, raw_entry in sorted(_json_dict(node.config).items()):
        title = checker.replace("_", " ")
        spec = spec_for(checker)
        if not spec or not isinstance(raw_entry, dict):
            # A checker with no scorer, or an entry that is not even a mapping.
            # Either way nothing inside it can be read, so the whole entry is
            # named rather than its keys.
            unread.append(UnreadKey(checker=checker, key="", label=checker))
            continue
        known = {field.name for field in spec}
        values = [
            PolicyValue(label=field.label, value=_format_policy_value(field, raw_entry[field.name]))
            for field in spec
            if field.name in raw_entry
        ]
        if values:
            reason = _inactive_reason(checker, raw_entry)
            section = PolicySection(
                checker=checker, title=title, values=values, inactive_reason=reason
            )
            (inactive if reason else sections).append(section)
        unread.extend(
            UnreadKey(checker=checker, key=key, label=f"{checker} \u2192 {key}")
            for key in sorted(raw_entry)
            if key not in known
        )
    return EffectivePolicy(sections=sections, inactive=inactive, unread=unread)
