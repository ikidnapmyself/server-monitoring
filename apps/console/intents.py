"""What each action intent does in the console: a link, a POST button, or text.

Config intents point at the admin until each domain slice gives them a console page.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.urls import NoReverseMatch, reverse

from config.describing import Action


@dataclass(frozen=True)
class Intent:
    """``permission`` is one codename, or a tuple of which any one suffices."""

    url: Callable[[dict[str, Any]], str]
    method: str
    permission: str | tuple[str, ...]

    @property
    def permissions(self) -> tuple[str, ...]:
        return (self.permission,) if isinstance(self.permission, str) else self.permission

    def allows(self, user) -> bool:
        return not self.permission or any(user.has_perm(p) for p in self.permissions)


@dataclass(frozen=True)
class ResolvedAction:
    text: str
    url: str = ""
    method: str = ""


def _page(name: str, filter_param: str = "", param: str = "") -> Callable[[dict[str, Any]], str]:
    def build(params: dict[str, Any]) -> str:
        url = reverse(name)
        if params.get(param):
            url = f"{url}?{urlencode({filter_param: params[param]})}"
        return url

    return build


def _record(name: str) -> Callable[[dict[str, Any]], str]:
    return lambda params: reverse(name, args=[params["pk"]])


def _incident_action(action: str) -> Callable[[dict[str, Any]], str]:
    return lambda params: reverse("console:incident-action", args=[params["pk"], action])


def _run_action(action: str) -> Callable[[dict[str, Any]], str]:
    return lambda params: reverse("console:run-action", args=[params["pk"], action])


def _external(params: dict[str, Any]) -> str:
    url = str(params["url"])
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) link: {url!r}")
    return url


INTENTS: dict[str, Intent] = {
    "open_url": Intent(_external, "get", ""),
    "edit_route": Intent(
        _page("admin:orchestration_pipelinedefinition_changelist"),
        "get",
        "orchestration.change_pipelinedefinition",
    ),
    "edit_channel": Intent(
        _page("admin:notify_notificationchannel_changelist", "q", "name"),
        "get",
        "notify.change_notificationchannel",
    ),
    "edit_provider": Intent(
        _page("admin:intelligence_intelligenceprovider_changelist"),
        "get",
        "intelligence.change_intelligenceprovider",
    ),
    "view_preflight": Intent(
        _page("admin:checkers_preflightrun_changelist"), "get", "checkers.view_preflightrun"
    ),
    "view_inbox": Intent(
        _page("console:inbox", "status", "status"),
        "get",
        ("orchestration.view_inboxitem", "orchestration.change_inboxitem"),
    ),
    "view_nodes": Intent(_page("admin:alerts_node_changelist"), "get", "alerts.view_node"),
    "view_node": Intent(_record("admin:alerts_node_change"), "get", "alerts.view_node"),
    "view_run": Intent(
        _record("console:run"),
        "get",
        ("orchestration.view_pipelinerun", "orchestration.change_pipelinerun"),
    ),
    "view_alert": Intent(
        _record("console:alert"), "get", ("alerts.view_alert", "alerts.change_alert")
    ),
    "reevaluate_alert": Intent(_record("console:alert-reeval"), "get", "alerts.change_alert"),
    "acknowledge": Intent(_incident_action("acknowledge"), "post", "alerts.change_incident"),
    "resolve": Intent(_incident_action("resolve"), "post", "alerts.change_incident"),
    "close": Intent(_incident_action("close"), "post", "alerts.change_incident"),
    "rerun_check": Intent(_record("console:incident-rerun"), "post", "checkers.add_checkrun"),
    "retry_run": Intent(_run_action("retry"), "post", "orchestration.change_pipelinerun"),
    "abandon_run": Intent(_run_action("abandon"), "post", "orchestration.change_pipelinerun"),
    "drain_run": Intent(_record("console:inbox-drain"), "post", "orchestration.change_inboxitem"),
    "drain_inbox": Intent(
        _page("console:inbox-drain-oldest"), "post", "orchestration.change_inboxitem"
    ),
    "reclaim_inbox": Intent(
        _page("console:inbox-reclaim"), "post", "orchestration.change_inboxitem"
    ),
}


def admin_url(name: str) -> str:
    """The admin's own pages the console chrome links to: index, login, logout."""
    return reverse(f"admin:{name}")


def resolve_action(action: Action, user) -> ResolvedAction:
    intent = INTENTS.get(action.intent)
    if intent is None or not intent.allows(user):
        return ResolvedAction(action.text)
    try:
        url = intent.url(action.params)
    except (KeyError, ValueError, NoReverseMatch):
        return ResolvedAction(action.text)
    return ResolvedAction(action.text, url, intent.method)
