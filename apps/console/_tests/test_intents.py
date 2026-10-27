import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
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


@pytest.mark.django_db
def test_unknown_intent_is_text(active_staff):
    assert resolve_action(Action("Do it", intent="nope"), active_staff) == ResolvedAction("Do it")


@pytest.mark.django_db
def test_no_intent_is_text(active_staff):
    assert resolve_action(Action("Just words"), active_staff) == ResolvedAction("Just words")


@pytest.mark.django_db
def test_missing_permission_is_text(active_staff):
    resolved = resolve_action(Action("Fix", intent="edit_channel"), active_staff)
    assert (resolved.url, resolved.method) == ("", "")


@pytest.mark.django_db
def test_permitted_link(admin_user):
    resolved = resolve_action(Action("Fix", intent="edit_channel"), admin_user)
    assert resolved == ResolvedAction("Fix", "/console/channels/", "get")


@pytest.mark.django_db
def test_named_channel_is_searched(admin_user):
    action = Action("Fix", intent="edit_channel", params={"name": "ops & on-call"})
    resolved = resolve_action(action, admin_user)
    assert resolved.url == "/console/channels/?q=ops+%26+on-call"


@pytest.mark.django_db
@pytest.mark.parametrize("intent", ["acknowledge", "resolve", "close"])
def test_post_intent(admin_user, intent):
    incident = Incident.objects.create(severity="critical")
    resolved = resolve_action(Action("Act", intent=intent, params={"pk": incident.pk}), admin_user)
    assert resolved.method == "post"
    assert resolved.url == f"/console/incidents/{incident.pk}/{intent}/"


@pytest.mark.django_db
def test_open_url_needs_no_permission(active_staff):
    action = Action("Open the runbook", intent="open_url", params={"url": "https://x.test/rb"})
    assert resolve_action(action, active_staff) == ResolvedAction(
        "Open the runbook", "https://x.test/rb", "get"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.test", "data:text/html,x"])
def test_open_url_rejects_non_http(active_staff, url):
    action = Action("Open", intent="open_url", params={"url": url})
    assert resolve_action(action, active_staff) == ResolvedAction("Open")


@pytest.mark.django_db
def test_missing_param_is_text(admin_user):
    assert resolve_action(Action("Node", intent="view_node"), admin_user) == ResolvedAction("Node")


@pytest.mark.django_db
def test_unreversible_is_text(admin_user, monkeypatch):
    from django.urls import reverse

    monkeypatch.setitem(
        INTENTS, "gone", Intent(lambda p: reverse("console:no-such-page"), "get", "")
    )
    assert resolve_action(Action("Gone", intent="gone"), admin_user) == ResolvedAction("Gone")


@pytest.mark.django_db
def test_query_params_are_encoded(admin_user):
    action = Action("Inbox", intent="view_inbox", params={"status": "a&b"})
    resolved = resolve_action(action, admin_user)
    assert resolved.url == "/console/inbox/?status=a%26b"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_preflight", {}, "/console/preflight/"),
        ("view_preflight_run", {"pk": 7}, "/console/preflight/7/"),
        ("view_analysis", {"pk": 7}, "/console/analyses/7/"),
    ],
)
def test_report_intents_point_at_the_console(admin_user, intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params), admin_user)
    assert resolved == ResolvedAction("Go", url, "get")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "params", "url"),
    [
        ("view_alert", {"pk": 7}, "/console/alerts/7/"),
        ("reevaluate_alert", {"pk": 7}, "/console/alerts/7/reevaluate/"),
    ],
)
def test_alert_intents_point_at_the_console(admin_user, intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params), admin_user)
    assert resolved == ResolvedAction("Go", url, "get")


@pytest.mark.django_db
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
def test_run_intents_point_at_the_console(admin_user, intent, url, method):
    resolved = resolve_action(Action("Go", intent=intent, params={"pk": 7}), admin_user)
    assert resolved == ResolvedAction("Go", url, method)


@pytest.mark.django_db
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
def test_route_intents_point_at_the_route_pages(admin_user, intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params), admin_user)
    assert resolved == ResolvedAction("Go", url, "get")


@pytest.mark.django_db
def test_a_route_viewer_can_open_a_route_but_not_edit_add_or_delete_one(django_user_model):
    user = _user_with(django_user_model, "orchestration.view_pipelinedefinition")

    assert resolve_action(Action("Go", intent="view_route"), user).url == "/console/routes/"
    view = Action("Go", intent="view_route", params={"pk": 7})
    assert resolve_action(view, user).url == "/console/routes/7/"
    assert resolve_action(Action("Go", intent="edit_route", params={"pk": 7}), user).url == ""
    assert resolve_action(Action("Go", intent="add_route"), user).url == ""
    assert resolve_action(Action("Go", intent="delete_route", params={"pk": 7}), user).url == ""


@pytest.mark.django_db
def test_a_channel_named_by_pk_and_name_opens_its_page(admin_user):
    action = Action("Open ops", intent="edit_channel", params={"pk": 7, "name": "ops"})
    resolved = resolve_action(action, admin_user)
    assert resolved.url == "/console/channels/7/"


@pytest.mark.django_db
def test_the_channel_intents_need_their_own_permissions(django_user_model):
    user = _user_with(django_user_model, "notify.view_notificationchannel")

    go = Action("Go", intent="view_channel", params={"name": "ops"})
    assert resolve_action(go, user).url == "/console/channels/?q=ops"
    view = Action("Go", intent="view_channel", params={"pk": 7})
    assert resolve_action(view, user).url == "/console/channels/7/"
    assert resolve_action(Action("Go", intent="edit_channel", params={"pk": 7}), user).url == ""
    assert resolve_action(Action("Go", intent="add_channel"), user).url == ""
    assert resolve_action(Action("Go", intent="delete_channel", params={"pk": 7}), user).url == ""


@pytest.mark.django_db
def test_add_channel_opens_the_driver_picker(admin_user):
    assert resolve_action(Action("Add", intent="add_channel"), admin_user).url == (
        "/console/channels/new/"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "params", "url", "method"),
    [
        ("view_inbox", {}, "/console/inbox/", "get"),
        ("drain_run", {"pk": 7}, "/console/inbox/7/drain/", "post"),
        ("drain_inbox", {}, "/console/inbox/drain-oldest/", "post"),
        ("reclaim_inbox", {}, "/console/inbox/reclaim/", "post"),
    ],
)
def test_inbox_intents_point_at_the_console(admin_user, intent, params, url, method):
    resolved = resolve_action(Action("Go", intent=intent, params=params), admin_user)
    assert resolved == ResolvedAction("Go", url, method)


@pytest.mark.django_db
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
        ("reevaluate_node", {"pk": 7}, "/console/nodes/7/reevaluate/", "get"),
        (
            "reevaluate_node",
            {"pk": 7, "checker": "disk inodes"},
            "/console/nodes/7/reevaluate/?checker=disk+inodes",
            "get",
        ),
    ],
)
def test_node_intents_point_at_the_console(admin_user, intent, params, url, method):
    resolved = resolve_action(Action("Go", intent=intent, params=params), admin_user)
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
def test_list_intents_carry_only_valid_filters(admin_user, intent, params, url):
    resolved = resolve_action(Action("Go", intent=intent, params=params), admin_user)
    assert resolved == ResolvedAction("Go", url, "get")


@pytest.mark.django_db
def test_every_permission_exists():
    for name, intent in INTENTS.items():
        for permission in intent.permissions if intent.permission else ():
            app_label, codename = permission.split(".")
            assert Permission.objects.filter(
                content_type__app_label=app_label, codename=codename
            ).exists(), name


def _user_with(django_user_model, codename):
    app_label, code = codename.split(".")
    user = django_user_model.objects.create_user("only", is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label=app_label, codename=code)
    )
    return django_user_model.objects.get(pk=user.pk)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "params", "permission"),
    [
        (intent, params, f"{app}.{verb}_{model}")
        for intent, params, app, model in (
            ("view_run", {"pk": 7}, "orchestration", "pipelinerun"),
            ("view_runs", {}, "orchestration", "pipelinerun"),
            ("view_incident", {"pk": 7}, "alerts", "incident"),
            ("view_incidents", {}, "alerts", "incident"),
            ("view_inbox", {}, "orchestration", "inboxitem"),
            ("view_alert", {"pk": 7}, "alerts", "alert"),
            ("view_nodes", {}, "alerts", "node"),
            ("view_node", {"pk": 7}, "alerts", "node"),
            ("view_policy", {}, "alerts", "node"),
            ("view_check_run", {"pk": 7}, "checkers", "checkrun"),
            ("view_checks", {}, "checkers", "checkrun"),
            ("view_preflight", {}, "checkers", "preflightrun"),
            ("view_preflight_run", {"pk": 7}, "checkers", "preflightrun"),
            ("view_analysis", {"pk": 7}, "intelligence", "analysisrun"),
            ("view_analyses", {}, "intelligence", "analysisrun"),
        )
        for verb in ("view", "change")
    ],
)
def test_a_read_link_takes_view_or_change(django_user_model, intent, params, permission):
    user = _user_with(django_user_model, permission)

    assert resolve_action(Action("Go", intent=intent, params=params), user).url


@pytest.mark.django_db
def test_a_tuple_permission_refuses_a_user_with_none_of_them(active_staff):
    assert resolve_action(Action("Go", intent="view_run", params={"pk": 7}), active_staff).url == ""


@pytest.mark.django_db
def test_a_single_permission_still_works(django_user_model):
    user = _user_with(django_user_model, "notify.add_notificationchannel")

    assert resolve_action(Action("Add", intent="add_channel"), user).url
    assert resolve_action(Action("Fix", intent="edit_channel"), user).url == ""


def test_every_method_is_get_or_post():
    assert {intent.method for intent in INTENTS.values()} <= {"get", "post"}


def _render(user, action):
    request = RequestFactory().get("/console/")
    request.user = user
    template = Template(
        "{% load console %}{% resolved action as r %}{% if r %}{{ r.method }}|{{ r.url }}"
        "{% else %}none{% endif %}"
    )
    return template.render(Context({"request": request, "action": action}))


@pytest.mark.django_db
def test_tag_resolves_for_the_request_user(admin_user):
    rendered = _render(admin_user, Action("Fix", intent="edit_route"))
    assert rendered == "get|/console/routes/"


@pytest.mark.django_db
def test_tag_passes_over_no_action(admin_user):
    assert _render(admin_user, None) == "none"


@pytest.mark.django_db
def test_rerun_check_posts_to_the_incident_rerun(admin_user):
    incident = Incident.objects.create(severity="critical")
    action = Action("Run again", intent="rerun_check", params={"pk": incident.pk})
    resolved = resolve_action(action, admin_user)
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


def test_admin_url_names_the_admins_own_pages():
    assert admin_url("login") == reverse("admin:login")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("perm", "intent"),
    [
        ("orchestration.change_pipelinedefinition", "edit_route"),
        ("notify.change_notificationchannel", "edit_channel"),
    ],
)
def test_an_edit_link_needs_the_change_permission(django_user_model, perm, intent):
    user = _user_with(django_user_model, perm)

    assert resolve_action(Action("Fix", intent=intent, params={"pk": 7}), user).url


def test_the_list_intents_are_gone():
    assert not {"view_routes", "view_channels"} & set(INTENTS)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "params", "permission", "url"),
    [
        ("view_provider", {}, "intelligence.view_intelligenceprovider", "/console/providers/"),
        (
            "view_provider",
            {"pk": 7},
            "intelligence.view_intelligenceprovider",
            "/console/providers/7/",
        ),
        (
            "view_provider",
            {"pk": 7},
            "intelligence.change_intelligenceprovider",
            "/console/providers/7/",
        ),
        ("edit_provider", {}, "intelligence.change_intelligenceprovider", "/console/providers/"),
        (
            "edit_provider",
            {"pk": 7},
            "intelligence.change_intelligenceprovider",
            "/console/providers/7/",
        ),
        ("add_provider", {}, "intelligence.add_intelligenceprovider", "/console/providers/new/"),
        (
            "delete_provider",
            {"pk": 7},
            "intelligence.delete_intelligenceprovider",
            "/console/providers/7/delete/",
        ),
    ],
)
def test_the_provider_intents_point_at_the_provider_pages(
    django_user_model, intent, params, permission, url
):
    user = _user_with(django_user_model, permission)

    assert resolve_action(Action("Go", intent=intent, params=params), user) == ResolvedAction(
        "Go", url, "get"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "params", "permission", "url"),
    [
        ("view_key", {}, "config_app.view_apikey", "/console/keys/"),
        ("view_key", {"pk": 7}, "config_app.view_apikey", "/console/keys/7/"),
        ("view_key", {"pk": 7}, "config_app.change_apikey", "/console/keys/7/"),
        ("edit_key", {"pk": 7}, "config_app.change_apikey", "/console/keys/7/"),
        ("add_key", {}, "config_app.add_apikey", "/console/keys/new/"),
        ("delete_key", {"pk": 7}, "config_app.delete_apikey", "/console/keys/7/delete/"),
    ],
)
def test_the_key_intents_point_at_the_key_pages(django_user_model, intent, params, permission, url):
    user = _user_with(django_user_model, permission)

    assert resolve_action(Action("Go", intent=intent, params=params), user) == ResolvedAction(
        "Go", url, "get"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "allowed"),
    [
        ("view_provider", {"view", "change"}),
        ("edit_provider", {"change"}),
        ("add_provider", {"add"}),
        ("delete_provider", {"delete"}),
    ],
)
@pytest.mark.parametrize("verb", ["view", "change", "add", "delete"])
def test_each_provider_intent_takes_its_own_permission(django_user_model, intent, allowed, verb):
    user = _user_with(django_user_model, f"intelligence.{verb}_intelligenceprovider")

    assert INTENTS[intent].allows(user) is (verb in allowed)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "allowed"),
    [
        ("view_key", {"view", "change"}),
        ("edit_key", {"change"}),
        ("add_key", {"add"}),
        ("delete_key", {"delete"}),
    ],
)
@pytest.mark.parametrize("verb", ["view", "change", "add", "delete"])
def test_each_key_intent_takes_its_own_permission(django_user_model, intent, allowed, verb):
    user = _user_with(django_user_model, f"config_app.{verb}_apikey")

    assert INTENTS[intent].allows(user) is (verb in allowed)


def _chrome(user, template):
    request = RequestFactory().get("/console/")
    request.user = user
    return render_to_string(template, {"nav": "system"}, request=request)


def _system_item(user) -> str:
    found = re.search(
        r'<a class="c-unav__item" href="([^"]+)"[^>]*>(?:(?!</a>).)*<span>System</span>',
        _chrome(user, "console/base.html"),
        re.DOTALL,
    )
    return found.group(1) if found else ""


def _tabs(user) -> list[str]:
    return re.findall(r">([^<>]+)</a>", _chrome(user, "console/partials/_system_nav.html"))


SYSTEM_READERS = [
    ("orchestration.view_pipelinedefinition", ["Routes"], "/console/routes/"),
    ("orchestration.change_pipelinedefinition", ["Routes"], "/console/routes/"),
    ("notify.view_notificationchannel", ["Channels"], "/console/channels/"),
    ("notify.change_notificationchannel", ["Channels"], "/console/channels/"),
    ("intelligence.view_intelligenceprovider", ["Providers"], "/console/providers/"),
    ("intelligence.change_intelligenceprovider", ["Providers"], "/console/providers/"),
    ("config_app.view_apikey", ["API keys"], "/console/keys/"),
    ("config_app.change_apikey", ["API keys"], "/console/keys/"),
    ("intelligence.add_intelligenceprovider", [], ""),
    ("config_app.delete_apikey", [], ""),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("permission", "tabs", "system"), SYSTEM_READERS)
def test_a_single_permission_reader_gets_only_its_tab(django_user_model, permission, tabs, system):
    user = _user_with(django_user_model, permission)

    assert _tabs(user) == tabs
    assert _system_item(user) == system


@pytest.mark.django_db
def test_a_reader_of_everything_gets_every_tab_in_order(admin_user):
    assert _tabs(admin_user) == ["Routes", "Channels", "Providers", "API keys"]
    assert _system_item(admin_user) == "/console/routes/"
