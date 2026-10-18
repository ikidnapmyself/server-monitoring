import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.template import Context, Template
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
    assert {"open_url", "edit_channel", "edit_route", "view_inbox", "view_nodes"} <= (
        emitted_intents()
    )


def test_every_emitted_intent_is_known():
    assert emitted_intents() <= set(INTENTS)


@pytest.mark.django_db
def test_unknown_intent_is_text(staff):
    assert resolve_action(Action("Do it", intent="nope"), staff) == ResolvedAction("Do it")


@pytest.mark.django_db
def test_no_intent_is_text(staff):
    assert resolve_action(Action("Just words"), staff) == ResolvedAction("Just words")


@pytest.mark.django_db
def test_missing_permission_is_text(staff):
    resolved = resolve_action(Action("Fix", intent="edit_channel"), staff)
    assert (resolved.url, resolved.method) == ("", "")


@pytest.mark.django_db
def test_permitted_link(admin_user):
    resolved = resolve_action(Action("Fix", intent="edit_channel"), admin_user)
    assert resolved == ResolvedAction("Fix", "/admin/notify/notificationchannel/", "get")


@pytest.mark.django_db
def test_named_channel_is_searched(admin_user):
    action = Action("Fix", intent="edit_channel", params={"name": "ops & on-call"})
    resolved = resolve_action(action, admin_user)
    assert resolved.url == "/admin/notify/notificationchannel/?q=ops+%26+on-call"


@pytest.mark.django_db
@pytest.mark.parametrize("intent", ["acknowledge", "resolve", "close"])
def test_post_intent(admin_user, intent):
    incident = Incident.objects.create(severity="critical")
    resolved = resolve_action(Action("Act", intent=intent, params={"pk": incident.pk}), admin_user)
    assert resolved.method == "post"
    assert resolved.url == f"/console/incidents/{incident.pk}/{intent}/"


@pytest.mark.django_db
def test_open_url_needs_no_permission(staff):
    action = Action("Open the runbook", intent="open_url", params={"url": "https://x.test/rb"})
    assert resolve_action(action, staff) == ResolvedAction(
        "Open the runbook", "https://x.test/rb", "get"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.test", "data:text/html,x"])
def test_open_url_rejects_non_http(staff, url):
    action = Action("Open", intent="open_url", params={"url": url})
    assert resolve_action(action, staff) == ResolvedAction("Open")


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
        ("edit_route", {}, "/admin/orchestration/pipelinedefinition/"),
        ("edit_provider", {}, "/admin/intelligence/intelligenceprovider/"),
        ("view_preflight", {}, "/admin/checkers/preflightrun/"),
        ("view_check_run", {"pk": 7}, "/admin/checkers/checkrun/7/change/"),
        ("view_preflight_run", {"pk": 7}, "/admin/checkers/preflightrun/7/change/"),
    ],
)
def test_config_intents_point_at_the_admin(admin_user, intent, params, url):
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
        )
        for verb in ("view", "change")
    ],
)
def test_a_read_link_takes_view_or_change(django_user_model, intent, params, permission):
    user = _user_with(django_user_model, permission)

    assert resolve_action(Action("Go", intent=intent, params=params), user).url


@pytest.mark.django_db
def test_a_tuple_permission_refuses_a_user_with_none_of_them(staff):
    assert resolve_action(Action("Go", intent="view_run", params={"pk": 7}), staff).url == ""


@pytest.mark.django_db
def test_a_single_permission_still_works(django_user_model):
    user = _user_with(django_user_model, "notify.change_notificationchannel")

    assert resolve_action(Action("Fix", intent="edit_channel"), user).url
    assert resolve_action(Action("Fix", intent="edit_route"), user).url == ""


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
    assert rendered == "get|/admin/orchestration/pipelinedefinition/"


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
