import json
import re
from unittest import mock

import pytest
from django.contrib.auth.models import Permission
from django.db import IntegrityError, OperationalError
from django.urls import reverse

from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries.routes import build_route_editor
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.routing import StaleRoute
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db

STALE = "This route changed since you opened it. Here it is now."
CLUSTER = {"field": "source", "op": "is", "value": "cluster"}
ODD = {"field": "source", "op": "zap", "value": "x"}


@pytest.fixture(autouse=True)
def _empty_table():
    clear_lanes()


@pytest.fixture
def route():
    return PipelineDefinition.objects.create(
        name="web", priority=50, stages=["check", "notify"], match=[CLUSTER]
    )


def _grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="orchestration", codename=codename)
        )
    return user


@pytest.fixture
def route_viewer(client, bare_staff):
    return _grant(bare_staff, "view_pipelinedefinition")


@pytest.fixture
def route_editor(client, bare_staff):
    return _grant(bare_staff, "change_pipelinedefinition")


@pytest.fixture
def route_adder(client, bare_staff):
    return _grant(bare_staff, "view_pipelinedefinition", "add_pipelinedefinition")


def _url(route=None):
    if route is None:
        return reverse("console:route-new")
    return reverse("console:route", args=[route.pk])


def _data(route=None, conditions=(CLUSTER,), **overrides):
    data = {
        "name": route.name if route else "db",
        "description": "",
        "is_active": "on",
        "priority": "50",
        "channel": "",
        "stages": ["check", "notify"],
        "version": str(route.version) if route else "1",
        "cond-TOTAL_FORMS": str(len(conditions)),
        "cond-INITIAL_FORMS": "0",
    }
    for i, cond in enumerate(conditions):
        row = {"label_key": "", **cond}
        for key, value in row.items():
            data[f"cond-{i}-{key}"] = value
    data.update(overrides)
    return data


def test_the_editor_urls():
    assert reverse("console:route-new") == "/console/routes/new/"
    assert reverse("console:route", args=[7]) == "/console/routes/7/"


def test_anonymous_is_sent_to_the_console_login(client, route):
    resp = client.get(_url(route))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next={_url(route)}"


def test_an_anonymous_save_logs_in_back_to_the_editor(client, route):
    resp = client.post(_url(route), _data(route))

    assert resp["Location"] == f"{reverse('console:login')}?next={_url(route)}"


def test_an_anonymous_add_logs_in_back_to_the_new_route_page(client):
    resp = client.post(_url(), _data())

    assert resp["Location"] == f"{reverse('console:login')}?next={_url()}"


def test_staff_without_a_route_permission_is_refused(client, bare_staff, route):
    assert client.get(_url(route)).status_code == 403


def test_the_editor_renders_the_route(client, superuser, route):
    client.force_login(superuser)

    resp = client.get(_url(route))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/route.html"
    assert "<title>Route · web · Console</title>" in body
    assert '<h1 class="c-pagehead__title">Route web</h1>' in body
    assert 'name="version" value="1"' in body
    assert 'name="cond-0-value" value="cluster"' in body
    assert 'name="cond-0-DELETE"' in body and ">Remove</label>" in body
    assert 'type="checkbox" name="stages" value="check"' in body
    assert "All of these must hold. No conditions matches everything." in body
    assert '<button id="route-add-condition"' in body and 'name="add_condition"' in body
    assert body.index('id="route-save"') < body.index('id="route-add-condition"')
    assert "data-disable-on-submit" in body
    assert '<a id="route-back" class="c-crumb" href="/console/routes/#route-' in body
    assert f'href="/console/routes/{route.pk}/delete/">Delete route</a>' in body
    assert "data-poll" not in body[body.index("<main") :]


def test_the_editor_reads_in_a_fixed_number_of_queries(
    client, superuser, route, django_assert_num_queries
):
    client.force_login(superuser)
    for i in range(3):
        NotificationChannel.objects.create(name=f"ch-{i}", driver="email", config={})
    client.get(_url(route))

    with django_assert_num_queries(4):
        assert client.get(_url(route)).status_code == 200


def test_the_details_hold_the_facts(client, superuser, route):
    client.force_login(superuser)
    PipelineDefinition.objects.filter(pk=route.pk).update(
        created_by="root", tags={"seed_shape": "record-only"}
    )

    body = client.get(_url(route)).content.decode()

    details = body[body.index('id="route-details"') :]
    assert "<dt>Created by</dt><dd>root</dd>" in details
    assert "<dt>Version</dt><dd>1</dd>" in details
    assert "<dt>Created</dt>" in details and "<dt>Updated</dt>" in details
    tags = json.dumps({"seed_shape": "record-only"})
    assert f"<dt>Tags</dt><dd>{tags.replace(chr(34), '&quot;')}</dd>" in details


def test_a_route_with_no_tags_or_author_leaves_them_out(client, superuser, route):
    client.force_login(superuser)

    body = client.get(_url(route)).content.decode()

    assert "<dt>Tags</dt>" not in body
    assert "<dt>Created by</dt>" not in body


def test_htmx_gets_the_editor_without_the_layout(client, superuser, route):
    client.force_login(superuser)

    resp = client.get(_url(route), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_route.html"
    assert "<html" not in resp.content.decode()


def test_the_page_carries_nothing_the_csp_blocks(client, superuser, route):
    client.force_login(superuser)

    body = client.get(_url(route)).content.decode()

    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_a_view_only_reader_sees_the_form_disabled(client, route_viewer, route):
    resp = client.get(_url(route))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert 'name="name" value="web"' in body
    assert body.count(" disabled") >= 5
    assert 'id="route-save"' not in body
    assert 'id="route-add-condition"' not in body
    assert '<a id="route-cancel" class="c-button" href="/console/routes/#route-' in body
    assert ">Back to routes</a>\n</form>" in body


def test_a_view_only_reader_is_refused_a_save(client, route_viewer, route):
    resp = client.post(_url(route), _data(route, name="renamed"))

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to change routes required."
    route.refresh_from_db()
    assert route.name == "web"


def test_a_change_only_user_opens_the_editor_enabled(client, route_editor, route):
    body = client.get(_url(route)).content.decode()

    assert 'id="route-save"' in body


def test_a_save_writes_the_route_and_lands_on_the_routes_page(client, superuser, route):
    client.force_login(superuser)
    ch = NotificationChannel.objects.create(name="ops", driver="email", is_active=True, config={})
    severity = {"field": "severity", "op": "in", "value": "critical, warning"}

    resp = client.post(
        _url(route), _data(route, conditions=(CLUSTER, severity), channel=str(ch.pk))
    )

    assert resp.status_code == 303
    assert resp["Location"] == f"{reverse('console:routes')}#route-{route.pk}"
    route.refresh_from_db()
    assert route.version == 2
    assert route.channel == ch
    assert route.match == [
        CLUSTER,
        {"field": "severity", "op": "in", "value": ["critical", "warning"]},
    ]
    assert "Route saved." in client.get(resp["Location"]).content.decode()


def test_a_stale_save_is_a_409_with_the_fresh_values(client, superuser, route):
    client.force_login(superuser)
    opened = _data(route, priority="60")
    PipelineDefinition.objects.filter(pk=route.pk).update(priority=70, version=2)

    resp = client.post(_url(route), opened)

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "This route changed since you opened it. Here it is now." in body
    assert 'name="priority" value="70"' in body
    assert 'name="version" value="2"' in body
    route.refresh_from_db()
    assert (route.priority, route.version) == (70, 2)


def test_a_mangled_version_is_a_stale_save(client, superuser, route):
    client.force_login(superuser)

    resp = client.post(_url(route), _data(route, version="soon"))

    assert resp.status_code == 409
    route.refresh_from_db()
    assert route.version == 1


def test_a_busy_database_is_a_409(client, superuser, route):
    client.force_login(superuser)
    with mock.patch(
        "apps.console.views.route.save_route", side_effect=OperationalError("database is locked")
    ):
        resp = client.post(_url(route), _data(route, priority="60"))

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "Couldn&#x27;t save just now. Here&#x27;s the route as it is." in body
    assert "changed since you opened it" not in body


def test_a_name_taken_in_a_race_is_a_name_error(client, superuser, route):
    client.force_login(superuser)
    PipelineDefinition.objects.create(name="db", priority=60)

    with mock.patch("apps.orchestration.forms.RouteForm.validate_unique"):
        resp = client.post(_url(route), _data(route, name="db"))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "Another route already has this name." in body
    route.refresh_from_db()
    assert route.name == "web"


def test_an_integrity_error_without_a_clashing_name_is_a_stale_save(client, superuser, route):
    client.force_login(superuser)
    with mock.patch(
        "apps.console.views.route.save_route", side_effect=IntegrityError("FOREIGN KEY constraint")
    ):
        resp = client.post(_url(route), _data(route, name="db"))

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "This route changed since you opened it. Here it is now." in body
    assert "Another route already has this name." not in body


def test_a_name_another_route_has_is_refused(client, superuser, route):
    client.force_login(superuser)
    PipelineDefinition.objects.create(name="db", priority=60)

    resp = client.post(_url(route), _data(route, name="db"))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert (
        '<p class="c-field__error c-tone-critical">Another route already has this name.</p>' in body
    )
    route.refresh_from_db()
    assert route.name == "web"


def test_add_condition_opens_a_row_and_saves_nothing(client, superuser, route):
    client.force_login(superuser)

    resp = client.post(_url(route), _data(route, priority="60", add_condition="1"))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert 'name="cond-1-value"' in body
    assert 'name="cond-TOTAL_FORMS" value="2"' in body
    assert 'name="priority" value="60"' in body
    assert '<option value="source" selected>Source</option>' in body
    assert "c-field__error" not in body
    assert "aria-invalid" not in body
    route.refresh_from_db()
    assert (route.priority, route.version) == (50, 1)


def test_a_bad_severity_re_renders_with_the_error(client, superuser, route):
    client.force_login(superuser)
    typo = {"field": "severity", "op": "is", "value": "critcal"}

    resp = client.post(_url(route), _data(route, conditions=(typo,)))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "Severity can&#x27;t be critcal." in body
    route.refresh_from_db()
    assert route.match == [CLUSTER]


def test_an_invalid_save_keeps_the_posted_version(client, superuser, route):
    client.force_login(superuser)

    PipelineDefinition.objects.filter(pk=route.pk).update(version=5)

    resp = client.post(_url(route), _data(route, name="", version="1"))

    assert resp.status_code == 200
    assert 'name="version" value="1"' in resp.content.decode()


def test_removing_a_condition_drops_it(client, superuser, route):
    client.force_login(superuser)

    client.post(_url(route), _data(route, **{"cond-0-DELETE": "on"}))

    route.refresh_from_db()
    assert route.match == []


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_route_is_the_console_404_page(client, superuser, method):
    client.force_login(superuser)

    resp = getattr(client, method)(reverse("console:route", args=[999]))

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This route doesn't exist. It may have been deleted." in body
    assert '<a id="not-found-back" href="/console/routes/">Back to routes</a>' in body


def test_a_route_deleted_while_open_answers_the_404(client, superuser, route):
    client.force_login(superuser)
    url, opened = _url(route), _data(route, priority="60")
    route.delete()

    resp = client.post(url, opened)

    assert resp.status_code == 404


def test_a_route_deleted_during_the_save_answers_the_404(client, superuser, route):
    client.force_login(superuser)
    editors = [build_route_editor(route.pk, _data(route), can_change=True), None]

    with (
        mock.patch("apps.console.views.route.build_route_editor", side_effect=editors),
        mock.patch("apps.console.views.route.save_route", side_effect=StaleRoute),
    ):
        resp = client.post(_url(route), _data(route))

    assert resp.status_code == 404


class TestAStoredMatchTheRowsCantShow:
    @pytest.fixture
    def odd(self):
        return PipelineDefinition.objects.create(name="odd", priority=50, match=[ODD])

    def test_it_warns_and_shows_them_in_details(self, client, superuser, odd):
        client.force_login(superuser)

        body = client.get(_url(odd)).content.decode()

        assert "These conditions use a shape the editor can't show. Saving keeps them." in body
        assert 'name="replace_match"' in body and "Replace them" in body
        stored = body[body.index('id="route-stored"') :]
        assert "data-details" in body[body.index('<details id="route-stored"') :][:80]
        assert "zap" in stored
        assert 'name="cond-0-value"' not in body
        assert "zap" not in visible(body)

    def test_saving_keeps_them(self, client, superuser, odd):
        client.force_login(superuser)

        resp = client.post(_url(odd), _data(odd, conditions=(), priority="60"))

        assert resp.status_code == 303
        odd.refresh_from_db()
        assert (odd.match, odd.priority) == ([ODD], 60)

    def test_replacing_them_writes_the_rows(self, client, superuser, odd):
        client.force_login(superuser)

        client.post(_url(odd), _data(odd, conditions=(CLUSTER,), replace_match="on"))

        odd.refresh_from_db()
        assert odd.match == [CLUSTER]

    def test_a_non_list_match_shows_whole(self, client, superuser):
        client.force_login(superuser)
        junk = PipelineDefinition.objects.create(name="junk", priority=50, match={"zap": 1})

        body = client.get(_url(junk)).content.decode()

        assert "zap" in body[body.index('id="route-stored"') :]


class TestANewRoute:
    def test_it_renders_empty(self, client, route_adder):
        resp = client.get(_url())

        body = resp.content.decode()
        assert resp.status_code == 200
        assert "<title>New route · Console</title>" in body
        assert '<h1 class="c-pagehead__title">New route</h1>' in body
        assert 'name="cond-TOTAL_FORMS" value="0"' in body
        assert 'id="route-details"' not in body

    def test_without_add_permission_it_is_refused(self, client, route_viewer):
        resp = client.get(_url())
        assert resp.status_code == 403
        assert resp.content.decode() == "Permission to add routes required."
        assert client.post(_url(), _data()).status_code == 403
        assert not PipelineDefinition.objects.exists()

    def test_a_save_creates_it_with_its_author(self, client, route_adder):
        resp = client.post(_url(), _data())

        route = PipelineDefinition.objects.get(name="db")
        assert resp.status_code == 303
        assert resp["Location"] == f"{reverse('console:routes')}#route-{route.pk}"
        assert (route.created_by, route.version) == ("bare", 1)
        assert route.match == [CLUSTER]
        assert route.stages == ["check", "notify"]
        assert "Route added." in client.get(resp["Location"]).content.decode()

    def test_an_invalid_one_saves_nothing(self, client, route_adder):
        resp = client.post(_url(), _data(name=""))

        assert resp.status_code == 200
        assert not PipelineDefinition.objects.exists()

    def test_add_condition_saves_nothing(self, client, route_adder):
        resp = client.post(_url(), _data(name="", add_condition="1"))

        body = resp.content.decode()
        assert resp.status_code == 200
        assert 'name="cond-1-value"' in body
        assert "c-field__error" not in body
        assert not PipelineDefinition.objects.exists()

    def test_a_name_taken_in_a_race_is_a_name_error(self, client, route_adder):
        with mock.patch(
            "apps.console.views.route.PipelineDefinition.objects.create",
            side_effect=IntegrityError("UNIQUE constraint"),
        ):
            resp = client.post(_url(), _data())

        assert resp.status_code == 200
        assert "Another route already has this name." in resp.content.decode()


def test_a_checkbox_sits_before_its_label_and_the_description_is_short(client, superuser, route):
    client.force_login(superuser)

    body = client.get(_url(route)).content.decode()

    assert re.search(
        r'<div class="c-check"><input type="checkbox" name="is_active"[^>]*>'
        r'<label class="c-field__label" for="id_is_active">On</label></div>',
        body,
    )
    assert re.search(r'<textarea name="description" cols="40" rows="3"', body)
    assert '<fieldset id="route-stages" class="c-fieldset">' in body
