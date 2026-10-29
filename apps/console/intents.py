"""What each action intent does in the console: a link, a POST button, or text."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.http import QueryDict
from django.urls import NoReverseMatch, reverse

from apps.alerts.node_policy import editor_anchor
from apps.console.filters import AnalysisFilter, CheckFilter, IncidentFilter, RunFilter
from config.describing import Action


@dataclass(frozen=True)
class Intent:
    url: Callable[[dict[str, Any]], str]
    method: str


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
    name: str,
    criteria: type[IncidentFilter] | type[RunFilter] | type[CheckFilter] | type[AnalysisFilter],
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


def _node_allow_ports(params: dict[str, Any]) -> str:
    url = reverse("console:node-allow-ports", args=[params["pk"]])
    return f"{url}?{urlencode({'ports': ','.join(str(p) for p in params['ports'])})}"


def _channel(params: dict[str, Any]) -> str:
    if params.get("pk"):
        return reverse("console:channel", args=[params["pk"]])
    return _page("console:channels", "q", "name")(params)


def _record_or_list(record: str, listing: str) -> Callable[[dict[str, Any]], str]:
    def build(params: dict[str, Any]) -> str:
        if params.get("pk"):
            return reverse(record, args=[params["pk"]])
        return reverse(listing)

    return build


_route = _record_or_list("console:route", "console:routes")
_provider = _record_or_list("console:provider", "console:providers")
_key = _record_or_list("console:key", "console:keys")
_user = _record_or_list("console:user", "console:users")


def _external(params: dict[str, Any]) -> str:
    url = str(params["url"])
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) link: {url!r}")
    return url


INTENTS: dict[str, Intent] = {
    "open_url": Intent(_external, "get"),
    "view_route": Intent(_route, "get"),
    "edit_route": Intent(_route, "get"),
    "add_route": Intent(_page("console:route-new"), "get"),
    "delete_route": Intent(_record("console:route-delete"), "get"),
    "view_channel": Intent(_channel, "get"),
    "edit_channel": Intent(_channel, "get"),
    "add_channel": Intent(_page("console:channel-new"), "get"),
    "delete_channel": Intent(_record("console:channel-delete"), "get"),
    "view_provider": Intent(_provider, "get"),
    "edit_provider": Intent(_provider, "get"),
    "add_provider": Intent(_page("console:provider-new"), "get"),
    "delete_provider": Intent(_record("console:provider-delete"), "get"),
    "view_key": Intent(_key, "get"),
    "edit_key": Intent(_key, "get"),
    "add_key": Intent(_page("console:key-new"), "get"),
    "delete_key": Intent(_record("console:key-delete"), "get"),
    "view_user": Intent(_user, "get"),
    "edit_user": Intent(_user, "get"),
    "set_user_password": Intent(_record("console:user-password"), "get"),
    "delete_user": Intent(_record("console:user-delete"), "get"),
    "add_user": Intent(_page("console:user-new"), "get"),
    "make_superuser": Intent(_record("console:user-make-superuser"), "post"),
    "view_preflight": Intent(_page("console:preflight"), "get"),
    "view_inbox": Intent(_page("console:inbox", "status", "status"), "get"),
    "view_nodes": Intent(_page("console:nodes"), "get"),
    "view_policy": Intent(_page("console:policy"), "get"),
    "view_node": Intent(_record("console:node"), "get"),
    "view_check_run": Intent(_record("console:check"), "get"),
    "view_checks": Intent(_filtered("console:checks", CheckFilter), "get"),
    "view_preflight_run": Intent(_record("console:preflight-run"), "get"),
    "edit_node_policy": Intent(_node_policy, "get"),
    "allow_node_ports": Intent(_node_allow_ports, "get"),
    "reevaluate_node": Intent(_node_reeval, "get"),
    "view_run": Intent(_record("console:run"), "get"),
    "view_analysis": Intent(_record("console:analysis"), "get"),
    "view_analyses": Intent(_filtered("console:analyses", AnalysisFilter), "get"),
    "view_runs": Intent(_filtered("console:runs", RunFilter), "get"),
    "view_incident": Intent(_record("console:incident"), "get"),
    "view_incidents": Intent(_filtered("console:incidents", IncidentFilter), "get"),
    "view_alert": Intent(_record("console:alert"), "get"),
    "reevaluate_alert": Intent(_record("console:alert-reeval"), "get"),
    "acknowledge": Intent(_incident_action("acknowledge"), "post"),
    "resolve": Intent(_incident_action("resolve"), "post"),
    "close": Intent(_incident_action("close"), "post"),
    "rerun_check": Intent(_record("console:incident-rerun"), "post"),
    "retry_run": Intent(_run_action("retry"), "post"),
    "abandon_run": Intent(_run_action("abandon"), "post"),
    "drain_run": Intent(_record("console:inbox-drain"), "post"),
    "drain_inbox": Intent(_page("console:inbox-drain-oldest"), "post"),
    "reclaim_inbox": Intent(_page("console:inbox-reclaim"), "post"),
}


def admin_url(name: str) -> str:
    """The admin page the console header links to, its index, until /admin/ goes."""
    return reverse(f"admin:{name}")


def resolve_action(action: Action) -> ResolvedAction:
    intent = INTENTS.get(action.intent)
    if intent is None:
        return ResolvedAction(action.text)
    try:
        url = intent.url(action.params)
    except (KeyError, ValueError, NoReverseMatch):
        return ResolvedAction(action.text)
    return ResolvedAction(action.text, url, intent.method)
