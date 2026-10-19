"""What each action intent does in the console: a link, a POST button, or text.

Config intents point at the admin until each domain slice gives them a console page.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.http import QueryDict
from django.urls import NoReverseMatch, reverse

from apps.alerts.node_policy import editor_anchor
from apps.console.filters import IncidentFilter, RunFilter
from config.describing import Action

NODE_READ = ("alerts.view_node", "alerts.change_node")
INCIDENT_READ = ("alerts.view_incident", "alerts.change_incident")
RUN_READ = ("orchestration.view_pipelinerun", "orchestration.change_pipelinerun")
ROUTE_READ = ("orchestration.view_pipelinedefinition", "orchestration.change_pipelinedefinition")
ROUTE_CHANGE = "orchestration.change_pipelinedefinition"
ROUTE_ADD = "orchestration.add_pipelinedefinition"
ROUTE_DELETE = "orchestration.delete_pipelinedefinition"
CHANNEL_READ = ("notify.view_notificationchannel", "notify.change_notificationchannel")
CHANNEL_CHANGE = "notify.change_notificationchannel"
CHANNEL_ADD = "notify.add_notificationchannel"
CHANNEL_DELETE = "notify.delete_notificationchannel"


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


def _filtered(
    name: str, criteria: type[IncidentFilter] | type[RunFilter]
) -> Callable[[dict[str, Any]], str]:
    def build(params: dict[str, Any]) -> str:
        query = QueryDict(mutable=True)
        for key, value in params.items():
            query[key] = str(value)
        return reverse(name) + criteria.parse(query).querystring()

    return build


def _incident_action(action: str) -> Callable[[dict[str, Any]], str]:
    return lambda params: reverse("console:incident-action", args=[params["pk"], action])


def _run_action(action: str) -> Callable[[dict[str, Any]], str]:
    return lambda params: reverse("console:run-action", args=[params["pk"], action])


def _node_policy(params: dict[str, Any]) -> str:
    url = reverse("console:node-policy", args=[params["pk"]])
    anchor = editor_anchor(str(params.get("checker", "")))
    return f"{url}#{anchor}" if anchor else url


def _node_reeval(params: dict[str, Any]) -> str:
    url = reverse("console:node-reeval", args=[params["pk"]])
    checker = params.get("checker")
    return f"{url}?{urlencode({'checker': checker})}" if checker else url


def _route(params: dict[str, Any]) -> str:
    if params.get("pk"):
        return reverse("console:route", args=[params["pk"]])
    return reverse("console:routes")


def _channel(params: dict[str, Any]) -> str:
    if params.get("pk"):
        return reverse("console:channel", args=[params["pk"]])
    return _page("console:channels", "q", "name")(params)


def _external(params: dict[str, Any]) -> str:
    url = str(params["url"])
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) link: {url!r}")
    return url


INTENTS: dict[str, Intent] = {
    "open_url": Intent(_external, "get", ""),
    "view_routes": Intent(_page("console:routes"), "get", ROUTE_READ),
    "edit_route": Intent(_route, "get", ROUTE_CHANGE),
    "add_route": Intent(_page("console:route-new"), "get", ROUTE_ADD),
    "delete_route": Intent(_record("console:route-delete"), "get", ROUTE_DELETE),
    "view_channels": Intent(_page("console:channels", "q", "name"), "get", CHANNEL_READ),
    "edit_channel": Intent(_channel, "get", CHANNEL_CHANGE),
    "add_channel": Intent(_page("console:channel-new"), "get", CHANNEL_ADD),
    "delete_channel": Intent(_record("console:channel-delete"), "get", CHANNEL_DELETE),
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
    "view_nodes": Intent(_page("console:nodes"), "get", NODE_READ),
    "view_policy": Intent(_page("console:policy"), "get", NODE_READ),
    "view_node": Intent(_record("console:node"), "get", NODE_READ),
    "view_check_run": Intent(
        _record("admin:checkers_checkrun_change"), "get", "checkers.view_checkrun"
    ),
    "view_preflight_run": Intent(
        _record("admin:checkers_preflightrun_change"), "get", "checkers.view_preflightrun"
    ),
    "edit_node_policy": Intent(_node_policy, "get", "alerts.change_node"),
    "reevaluate_node": Intent(_node_reeval, "get", "alerts.change_node"),
    "view_run": Intent(_record("console:run"), "get", RUN_READ),
    "view_runs": Intent(_filtered("console:runs", RunFilter), "get", RUN_READ),
    "view_incident": Intent(_record("console:incident"), "get", INCIDENT_READ),
    "view_incidents": Intent(_filtered("console:incidents", IncidentFilter), "get", INCIDENT_READ),
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
