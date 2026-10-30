import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template import Context, Template
from django.urls import reverse

from apps.alerts.models import Incident
from apps.console.intents import INTENTS, Intent, ResolvedAction, admin_url, resolve_action
from config.describing import Action

INTENT = re.compile(r"""intent=["']([a-z_]+)["']|Action\(\s*[^,()]+,\s*["']([a-z_]+)["']""")
NAMED = re.compile(r"""^_[A-Z_]+ = ["']([a-z_]+)["']$""", re.MULTILINE)


def emitted_intents() -> set[str]:
    root = Path(settings.BASE_DIR).resolve()
    found = set()
    for folder in ("apps", "config"):
        for path in (root / folder).rglob("*.py"):
            if "_tests" in path.parts:
                continue
            source = path.read_text()
            for match in INTENT.finditer(source):
                found.add(match.group(1) or match.group(2))
            if "Action(" in source:
                found.update(NAMED.findall(source))
    return found


def test_the_scan_sees_the_emitted_intents():
    assert {
        "open_url",
        "view_channel",
        "edit_channel",
        "view_route",
        "edit_route",
        "view_inbox",
        "view_nodes",
    } <= emitted_intents()


def test_every_emitted_intent_is_known():
    assert emitted_intents() <= set(INTENTS)


def test_unknown_intent_is_text():
    assert resolve_action(Action("Do it", intent="nope")) == ResolvedAction("Do it")


def test_no_intent_is_text():
    assert resolve_action(Action("Just words")) == ResolvedAction("Just words")


def test_a_known_intent_is_a_link():
    resolved = resolve_action(Action("Fix", intent="edit_channel"))
    assert resolved == ResolvedAction("Fix", "/console/channels/", "get")


def test_named_channel_is_searched():
    action = Action("Fix", intent="edit_channel", params={"name": "ops & on-call"})
    resolved = resolve_action(action)
    assert resolved.url == "/console/channels/?q=ops+%26+on-call"


@pytest.mark.django_db
@pytest.mark.parametrize("intent", ["acknowledge", "resolve", "close"])
def test_post_intent(intent):
    incident = Incident.objects.create(severity="critical")
    resolved = resolve_action(Action("Act", intent=intent, params={"pk": incident.pk}))
    assert resolved.method == "post"
    assert resolved.url == f"/console/incidents/{incident.pk}/{intent}/"


def test_open_url_is_a_link():
    action = Action("Open the runbook", intent="open_url", params={"url": "https://x.test/rb"})
    assert resolve_action(action) == ResolvedAction("Open the runbook", "https://x.test/rb", "get")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.test", "data:text/html,x"])
def test_open_url_rejects_non_http(url):
    action = Action("Open", intent="open_url", params={"url": url})
    assert resolve_action(action) == ResolvedAction("Open")


def test_missing_param_is_text():
    assert resolve_action(Action("Node", intent="view_node")) == ResolvedAction("Node")


def test_unreversible_is_text(monkeypatch):
    from django.urls import reverse

    monkeypatch.setitem(INTENTS, "gone", Intent(lambda p: reverse("console:no-such-page"), "get"))
    assert resolve_action(Action("Gone", intent="gone")) == ResolvedAction("Gone")


def test_query_params_are_encoded():
    action = Action("Inbox", intent="view_inbox", params={"status": "a&b"})
    resolved = resolve_action(action)
    assert resolved.url == "/console/inbox/?status=a%26b"


@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_preflight", {}, "/console/preflight/"),
        ("view_preflight_run", {"pk": 7}, "/console/preflight/7/"),
        ("view_analysis", {"pk": 7}, "/console/analyses/7/"),
    ],
)
def test_report_intents_point_at_the_console(intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params))
    assert resolved == ResolvedAction("Go", url, "get")


@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_alert", {"pk": 7}, "/console/alerts/7/"),
        ("reevaluate_alert", {"pk": 7}, "/console/alerts/7/reevaluate/"),
    ],
)
def test_alert_intents_point_at_the_console(intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params))
    assert resolved == ResolvedAction("Go", url, "get")


@pytest.mark.parametrize(
    ("intent", "url", "method"),
    [
        ("view_run", "/console/runs/7/", "get"),
        ("view_check_run", "/console/checks/7/", "get"),
        ("view_incident", "/console/incidents/7/", "get"),
        ("retry_run", "/console/runs/7/retry/", "post"),
        ("abandon_run", "/console/runs/7/abandon/", "post"),
    ],
)
def test_run_intents_point_at_the_console(intent, url, method):
    resolved = resolve_action(Action("Go", intent=intent, params={"pk": 7}))
    assert resolved == ResolvedAction("Go", url, method)


@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_route", {}, "/console/routes/"),
        ("view_route", {"pk": 7}, "/console/routes/7/"),
        ("edit_route", {}, "/console/routes/"),
        ("edit_route", {"pk": 7}, "/console/routes/7/"),
        ("add_route", {}, "/console/routes/new/"),
        ("delete_route", {"pk": 7}, "/console/routes/7/delete/"),
    ],
)
def test_route_intents_point_at_the_route_pages(intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params))
    assert resolved == ResolvedAction("Go", url, "get")


def test_a_channel_named_by_pk_and_name_opens_its_page():
    action = Action("Open ops", intent="edit_channel", params={"pk": 7, "name": "ops"})
    resolved = resolve_action(action)
    assert resolved.url == "/console/channels/7/"


def test_view_channel_opens_a_channel_by_name_or_pk():
    go = Action("Go", intent="view_channel", params={"name": "ops"})
    assert resolve_action(go).url == "/console/channels/?q=ops"
    view = Action("Go", intent="view_channel", params={"pk": 7})
    assert resolve_action(view).url == "/console/channels/7/"


def test_add_channel_opens_the_driver_picker():
    assert resolve_action(Action("Add", intent="add_channel")).url == "/console/channels/new/"


@pytest.mark.parametrize(
    ("intent", "params", "url", "method"),
    [
        ("view_inbox", {}, "/console/inbox/", "get"),
        ("drain_run", {"pk": 7}, "/console/inbox/7/drain/", "post"),
        ("drain_inbox", {}, "/console/inbox/drain-oldest/", "post"),
        ("reclaim_inbox", {}, "/console/inbox/reclaim/", "post"),
    ],
)
def test_inbox_intents_point_at_the_console(intent, params, url, method):
    resolved = resolve_action(Action("Go", intent=intent, params=params))
    assert resolved == ResolvedAction("Go", url, method)


@pytest.mark.parametrize(
    ("intent", "params", "url", "method"),
    [
        ("view_nodes", {}, "/console/nodes/", "get"),
        ("view_policy", {}, "/console/policy/", "get"),
        ("view_node", {"pk": 7}, "/console/nodes/7/", "get"),
        ("edit_node_policy", {"pk": 7}, "/console/nodes/7/policy/", "get"),
        (
            "edit_node_policy",
            {"pk": 7, "checker": "cpu"},
            "/console/nodes/7/policy/#policy-cpu",
            "get",
        ),
        ("edit_node_policy", {"pk": 7, "checker": "raid"}, "/console/nodes/7/policy/", "get"),
        (
            "allow_node_ports",
            {"pk": 7, "ports": [22, 80]},
            "/console/nodes/7/allow-ports/?ports=22%2C80",
            "get",
        ),
        ("reevaluate_node", {"pk": 7}, "/console/nodes/7/reevaluate/", "get"),
        (
            "reevaluate_node",
            {"pk": 7, "checker": "disk inodes"},
            "/console/nodes/7/reevaluate/?checker=disk+inodes",
            "get",
        ),
    ],
)
def test_node_intents_point_at_the_console(intent, params, url, method):
    resolved = resolve_action(Action("Go", intent=intent, params=params))
    assert resolved == ResolvedAction("Go", url, method)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_incidents", {}, "/console/incidents/"),
        (
            "view_incidents",
            {"severity": "critical", "node": 7},
            "/console/incidents/?severity=critical&node=7",
        ),
        ("view_incidents", {"severity": "loud"}, "/console/incidents/"),
        ("view_runs", {}, "/console/runs/"),
        ("view_runs", {"node": 7}, "/console/runs/?node=7"),
        (
            "view_checks",
            {"checker": "cpu", "status": "failing"},
            "/console/checks/?status=failing&checker=cpu",
        ),
        ("view_checks", {"checker": "never_heard"}, "/console/checks/"),
        (
            "view_analyses",
            {"provider": "local", "since": "7d"},
            "/console/analyses/?provider=local&since=7d",
        ),
    ],
)
def test_list_intents_carry_only_valid_filters(intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params))
    assert resolved == ResolvedAction("Go", url, "get")


def test_every_method_is_get_or_post():
    assert {intent.method for intent in INTENTS.values()} <= {"get", "post"}


def _render(action):
    template = Template(
        "{% load console %}{% resolved action as r %}{% if r %}{{ r.method }}|{{ r.url }}"
        "{% else %}none{% endif %}"
    )
    return template.render(Context({"action": action}))


def test_tag_resolves_the_action():
    rendered = _render(Action("Fix", intent="edit_route"))
    assert rendered == "get|/console/routes/"


def test_tag_passes_over_no_action():
    assert _render(None) == "none"


@pytest.mark.django_db
def test_rerun_check_posts_to_the_incident_rerun():
    incident = Incident.objects.create(severity="critical")
    action = Action("Run again", intent="rerun_check", params={"pk": incident.pk})
    resolved = resolve_action(action)
    assert resolved == ResolvedAction(
        "Run again", f"/console/incidents/{incident.pk}/rerun/", "post"
    )


ADMIN_IN_PYTHON = re.compile(r"""reverse\(\s*f?["']admin:""")
ADMIN_IN_TEMPLATE = re.compile(r"""/admin/|\{%\s*url\s+["']admin:""")


def test_only_intents_builds_admin_urls():
    root = Path(settings.BASE_DIR).resolve()
    console = root / "apps" / "console"
    offenders = [
        str(path.relative_to(root))
        for path in console.rglob("*.py")
        if "_tests" not in path.relative_to(console).parts
        and path.name != "intents.py"
        and ADMIN_IN_PYTHON.search(path.read_text())
    ] + [
        str(path.relative_to(root))
        for path in (root / "templates" / "console").rglob("*.html")
        if ADMIN_IN_TEMPLATE.search(path.read_text())
    ]

    assert offenders == []


def test_admin_url_names_the_admin_index():
    assert admin_url("index") == reverse("admin:index")


def test_the_list_intents_are_gone():
    assert not {"view_routes", "view_channels"} & set(INTENTS)


@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_provider", {}, "/console/providers/"),
        ("view_provider", {"pk": 7}, "/console/providers/7/"),
        ("edit_provider", {}, "/console/providers/"),
        ("edit_provider", {"pk": 7}, "/console/providers/7/"),
        ("add_provider", {}, "/console/providers/new/"),
        ("delete_provider", {"pk": 7}, "/console/providers/7/delete/"),
    ],
)
def test_the_provider_intents_point_at_the_provider_pages(intent, params, url):
    assert resolve_action(Action("Go", intent=intent, params=params)) == ResolvedAction(
        "Go", url, "get"
    )


@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_key", {}, "/console/keys/"),
        ("view_key", {"pk": 7}, "/console/keys/7/"),
        ("edit_key", {"pk": 7}, "/console/keys/7/"),
        ("add_key", {}, "/console/keys/new/"),
        ("delete_key", {"pk": 7}, "/console/keys/7/delete/"),
    ],
)
def test_the_key_intents_point_at_the_key_pages(intent, params, url):
    assert resolve_action(Action("Go", intent=intent, params=params)) == ResolvedAction(
        "Go", url, "get"
    )


@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_user", {}, "/console/users/"),
        ("view_user", {"pk": 7}, "/console/users/7/"),
        ("edit_user", {"pk": 7}, "/console/users/7/"),
        ("add_user", {}, "/console/users/new/"),
        ("set_user_password", {"pk": 7}, "/console/users/7/password/"),
        ("delete_user", {"pk": 7}, "/console/users/7/delete/"),
    ],
)
def test_the_user_intents_point_at_the_user_pages(intent, params, url):
    assert resolve_action(Action("Go", intent=intent, params=params)) == ResolvedAction(
        "Go", url, "get"
    )


@pytest.mark.django_db
def test_a_superuser_gets_every_system_tab_in_order(client, superuser):
    body = client.get(reverse("console:channels")).content.decode()

    nav = body[body.index('<nav class="c-subnav" aria-label="System">') :]
    tabs = re.findall(r">([^<>]+)</a>", nav[: nav.index("</nav>")])
    assert tabs == ["Routes", "Channels", "Providers", "API keys", "Users"]
    system = re.search(
        r'<a class="c-unav__item" href="([^"]+)"[^>]*>(?:(?!</a>).)*<span>System</span>',
        body,
        re.DOTALL,
    )
    assert system.group(1) == "/console/routes/"


def test_make_superuser_is_a_post():
    resolved = resolve_action(Action("Go", intent="make_superuser", params={"pk": 7}))
    assert resolved == ResolvedAction("Go", "/console/users/7/make-superuser/", "post")
