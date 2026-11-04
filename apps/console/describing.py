"""What an audit entry says: who did what to which record, worded when it's read."""

from dataclasses import dataclass
from typing import Any

from apps.alerts.describing import node_name
from apps.checkers.checkers import checker_label
from apps.console.models import AuditEntry
from apps.intelligence.providers import PROVIDERS
from apps.notify.drivers import DRIVER_REGISTRY
from apps.orchestration.models import PipelineStatus
from apps.orchestration.routing import describe_match
from config.describing import Action, Fact, format_list

KINDS = {
    "route": "route",
    "channel": "channel",
    "provider": "provider",
    "key": "API key",
    "user": "user",
    "incident": "incident",
    "run": "run",
    "node": "node",
    "alert": "alert",
    "inbox": "inbox",
}
UNKNOWN_KIND = "record"
KIND_INTENTS = {
    "route": "view_route",
    "channel": "view_channel",
    "provider": "view_provider",
    "key": "view_key",
    "user": "view_user",
    "incident": "view_incident",
    "run": "view_run",
    "node": "view_node",
    "alert": "view_alert",
}

# ``{target}`` is the record with its kind, ``{name}`` the name alone.
ACTIONS = {
    **{
        f"{kind}.{verb}": f"{done} {{target}}"
        for kind in ("route", "channel", "provider", "key", "user")
        for verb, done in (("add", "Added"), ("edit", "Edited"), ("delete", "Deleted"))
    },
    "user.set_password": "Set the password of {target}",  # nosec B105
    "user.promote": "Made {target} a superuser",
    "node.policy": "Edited the policy of {target}",
    "node.allow_ports": "Allowed ports {ports} on {target}",
    "node.reevaluate": "Re-evaluated the open alerts of {target}",
    "alert.reevaluate": "Re-evaluated {target}",
    "incident.acknowledge": "Acknowledged {target}",
    "incident.resolve": "Resolved {target}",
    "incident.close": "Closed {target}",
    "incident.rerun": "Ran the checks of {target} again",
    "run.retry": "Retried {target}",
    "run.abandon": "Marked {target} failed",
    "inbox.drain": "Drained {target}",
    "inbox.drain_oldest": "Drained the oldest queued runs",
    "inbox.reclaim": "Returned stuck runs to the queue",
    "session.sign_in": "Signed in",
    "session.sign_out": "Signed out",
    "session.sign_in_failed": "Failed sign-in as {name}",
}
FALLBACK = "Changed {target}"
NOBODY = "Not signed in"
BLANK_NAME = "a blank username"
MASKED = "Changed, value hidden"
HIDDEN = "hidden"
SOMETHING = "a record"
VALUE_LIMIT = 200
PORTLESS = "Allowed ports on {target}"
NOTHING_COUNTED = "Nothing changed."

FIELD_LABELS = {
    "is_active": "Active",
    "is_superuser": "Superuser",
    "first_name": "First name",
    "last_name": "Last name",
    "allowed_endpoints": "Allowed endpoints",
    "key": "Key",
    "password": "Password",  # nosec B105
    "driver": "Driver",
    "provider": "Provider",
    "match": "Conditions",
    "stages": "Stages",
    "email": "Email",
    "priority": "Priority",
}
CONFIG_LABELS: dict[str, str] = {}
_CONFIGURED: tuple[Any, ...] = (*DRIVER_REGISTRY.values(), *PROVIDERS.values())
for _cls in _CONFIGURED:
    for _spec in _cls.all_config_fields():
        CONFIG_LABELS.setdefault(f"config.{_spec.key}", _spec.label)
POLICY_ACTIONS = ("node.policy", "node.allow_ports")
POLICY_PORTS = "listening_ports"

COUNTS = {
    "changed": ("alert re-scored", "alerts re-scored"),
    "resolved": ("alert resolved", "alerts resolved"),
    "incidents_resolved": ("incident resolved", "incidents resolved"),
    "checks_run": ("check ran", "checks ran"),
    "alerts_created": ("alert opened", "alerts opened"),
    "alerts_updated": ("alert updated", "alerts updated"),
    "alerts_resolved": ("alert resolved", "alerts resolved"),
    "incidents_created": ("incident opened", "incidents opened"),
    "incidents_updated": ("incident updated", "incidents updated"),
    "runs": ("run queued", "runs queued"),
    "ran": ("run drained", "runs drained"),
    "failed": ("run raised an error", "runs raised an error"),
    "left": ("run still queued", "runs still queued"),
    "returned": ("run returned to the queue", "runs returned to the queue"),
}


@dataclass(frozen=True)
class Change:
    label: str
    old: str
    new: str
    secret: bool = False


@dataclass(frozen=True)
class EntryWords:
    before: str
    target: Action | None
    after: str
    summary: str = ""
    changes: tuple[Change, ...] = ()
    last_values: tuple[Fact, ...] = ()

    @property
    def headline(self) -> str:
        return f"{self.before}{self.target.text if self.target else ''}{self.after}"


def kind_word(kind: str) -> str:
    return KINDS.get(kind, UNKNOWN_KIND)


def actor_action(entry: AuditEntry) -> Action:
    if not entry.actor_name:
        return Action(NOBODY)
    if entry.actor_id is None:
        return Action(entry.actor_name)
    return Action(entry.actor_name, "view_user", {"pk": entry.actor_id})


def describe_entry(entry: AuditEntry, *, exists: bool) -> EntryWords:
    """``exists`` says whether the target's row is still there, so it can be linked."""
    template = ACTIONS.get(entry.action, FALLBACK)
    changes = entry.changes if isinstance(entry.changes, dict) else {}
    if "{ports}" in template:
        ports = _ports_added(changes.get(POLICY_PORTS))
        template = template.replace("{ports}", ports) if ports else PORTLESS
    before, target, after = _sentence(template, entry, exists)
    words = EntryWords(before=before, target=target, after=after)
    result = changes.get("result")
    fields = {k: v for k, v in changes.items() if k != "result"}
    summary = _counted(result) if isinstance(result, dict) else ""
    if entry.action.endswith(".delete"):
        return _with(words, summary, last_values=_last_values(entry.target_kind, fields))
    by_checker = entry.action in POLICY_ACTIONS
    rows = tuple(
        _change(entry.target_kind, field, value, by_checker)
        for field, value in fields.items()
        if isinstance(value, dict)
    )
    return _with(words, summary, changes=rows)


def _with(words: EntryWords, summary: str, **parts) -> EntryWords:
    return EntryWords(words.before, words.target, words.after, summary, **parts)


def _sentence(template: str, entry: AuditEntry, exists: bool):
    for placeholder in ("{target}", "{name}"):
        if placeholder in template:
            before, after = template.split(placeholder, 1)
            if placeholder == "{name}":
                return before, Action(entry.target_name or BLANK_NAME), after
            return before, _target(entry, exists), after
    return template, None, ""


def _target(entry: AuditEntry, exists: bool) -> Action:
    kind = entry.target_kind
    if kind == "inbox":
        return Action("the inbox", "view_inbox")
    if kind == "node":
        text = f"node {entry.target_name}" if entry.target_name else node_name("")
    elif not entry.target_name and entry.target_id is None:
        return Action(SOMETHING)
    elif entry.target_name:
        text = f"{kind_word(kind)} {entry.target_name}"
    else:
        text = f"{kind_word(kind)} {entry.target_id}"
    intent = KIND_INTENTS.get(kind)
    if not exists or intent is None or entry.target_id is None:
        return Action(text)
    return Action(text, intent, {"pk": entry.target_id})


def _label(field: str, by_checker: bool) -> str:
    if by_checker:
        return checker_label(field)
    if field in FIELD_LABELS:
        return FIELD_LABELS[field]
    if field in CONFIG_LABELS:
        return CONFIG_LABELS[field]
    return field.removeprefix("config.").replace("_", " ").replace(".", " ").capitalize()


def _value(value) -> str:
    text = _words(value)
    return text if len(text) <= VALUE_LIMIT else f"{text[: VALUE_LIMIT - 1]}\u2026"


def _words(value) -> str:
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(_words(item) for item in value) or "none"
    if isinstance(value, dict):
        parts = [f"{_label(str(k), False).lower()} {_words(v)}" for k, v in value.items()]
        return "; ".join(parts) or "none"
    return str(value) if value != "" else "blank"


def _change(kind: str, field: str, value: dict, by_checker: bool) -> Change:
    label = _label(field, by_checker)
    if value.get("changed"):
        return Change(label, MASKED, MASKED, secret=True)
    return Change(
        label,
        _field_value(kind, field, value.get("old")),
        _field_value(kind, field, value.get("new")),
    )


def _field_value(kind: str, field: str, value) -> str:
    """A stored value in the words its own page uses for it."""
    if value is None:
        return _value(value)
    if (kind, field) == ("route", "match"):
        return _value(describe_match(value))
    if (kind, field) == ("run", "status") and value in PipelineStatus.values:
        return str(PipelineStatus(value).label)
    return _value(value)


def _last_values(kind: str, fields: dict) -> tuple[Fact, ...]:
    return tuple(
        Fact(
            _label(field, False),
            HIDDEN if value.get("changed") else _field_value(kind, field, value.get("old")),
        )
        for field, value in fields.items()
        if isinstance(value, dict)
    )


def _counted(result: dict) -> str:
    parts = []
    for key, count in result.items():
        if not isinstance(count, int) or isinstance(count, bool) or not count:
            continue
        one, many = COUNTS.get(key, (key.replace("_", " "),) * 2)
        parts.append(f"{count} {one if count == 1 else many}")
    if not parts:
        return NOTHING_COUNTED
    text = format_list(parts, limit=len(parts))
    return f"{text[0].upper()}{text[1:]}."


def _ports_added(change) -> str:
    if not isinstance(change, dict):
        return ""
    old, new = _allowlist(change.get("old")), _allowlist(change.get("new"))
    added = [port for port in new if port not in old]
    return ", ".join(str(port) for port in added)


def _allowlist(policy) -> list:
    if not isinstance(policy, dict):
        return []
    ports = policy.get("allowlist")
    return ports if isinstance(ports, list) else []
