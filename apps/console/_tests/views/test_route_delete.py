import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.console._tests.test_guard import visible
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db

CLUSTER = {"field": "source", "op": "is", "value": "cluster"}


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
def route_editor(bare_staff):
    return _grant(bare_staff, "change_pipelinedefinition")


@pytest.fixture
def route_deleter(bare_staff):
    return _grant(bare_staff, "change_pipelinedefinition", "delete_pipelinedefinition")


def _url(route):
    return reverse("console:route-delete", args=[route.pk])


def test_the_delete_url():
    assert reverse("console:route-delete", args=[7]) == "/console/routes/7/delete/"


def test_anonymous_is_sent_to_the_console_login(client, route):
    resp = client.get(_url(route))

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("console:login"))


def test_an_anonymous_delete_logs_in_back_to_the_editor(client, route):
    resp = client.post(_url(route))

    assert resp.status_code == 302
    assert f"next=/console/routes/{route.pk}/" in resp.url
    assert PipelineDefinition.objects.filter(pk=route.pk).exists()


def test_the_page_names_the_route_and_where_its_traffic_goes(client, superuser, route):
    PipelineDefinition.objects.create(name="catch-all", priority=900, match=[])
    client.force_login(superuser)

    resp = client.get(_url(route))

    body = resp.content.decode()
    text = visible(body)
    assert resp.status_code == 200
    assert "Delete route web?" in text
    assert (
        "Traffic it matched goes to the next route that matches, at worst catch-all, "
        "which matches everything."
    ) in text
    form = body[body.index(f'<form method="post" action="{_url(route)}"') :]
    assert "data-disable-on-submit" in form[:100]
    assert '<button id="route-delete-confirm"' in form and "Delete route</button>" in form
    assert (
        f'<a id="route-delete-cancel" class="c-button" href="/console/routes/{route.pk}/"' in form
    )
    assert PipelineDefinition.objects.filter(pk=route.pk).exists()


def test_an_unrouted_fallthrough_is_said(client, superuser, route):
    client.force_login(superuser)

    text = visible(client.get(_url(route)).content.decode())

    assert "will match no route, and those runs will fail as unrouted." in text


def test_htmx_gets_the_page_without_the_layout(client, superuser, route):
    client.force_login(superuser)

    body = client.get(_url(route), headers={"HX-Request": "true"}).content.decode()

    assert "Delete route web?" in body
    assert "<html" not in body


def test_a_post_deletes_and_lands_on_the_routes_page(client, superuser, route):
    client.force_login(superuser)

    resp = client.post(_url(route))

    assert resp.status_code == 303
    assert resp.url == reverse("console:routes")
    assert not PipelineDefinition.objects.filter(pk=route.pk).exists()
    landed = client.get(resp.url).content.decode()
    assert "Route web deleted." in landed


def test_a_delete_permission_is_enough(client, route_deleter, route):
    assert client.get(_url(route)).status_code == 200
    assert client.post(_url(route)).status_code == 303


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_change_only_user_is_refused(client, route_editor, route, method):
    resp = getattr(client, method)(_url(route))

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to delete routes required."
    assert PipelineDefinition.objects.filter(pk=route.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_route_is_the_console_404_page(client, superuser, method):
    client.force_login(superuser)

    resp = getattr(client, method)(reverse("console:route-delete", args=[999]))

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This route doesn't exist. It may have been deleted." in body


class TestTheEditorLink:
    def test_a_deleter_sees_it(self, client, route_deleter, route):
        body = client.get(reverse("console:route", args=[route.pk])).content.decode()

        assert (
            f'<a id="route-delete" class="c-button c-button--sm c-button--danger" '
            f'href="{_url(route)}">Delete route</a>'
        ) in body

    def test_a_change_only_user_does_not(self, client, route_editor, route):
        body = client.get(reverse("console:route", args=[route.pk])).content.decode()

        assert 'id="route-delete"' not in body

    def test_a_new_route_has_none(self, client, superuser):
        client.force_login(superuser)

        body = client.get(reverse("console:route-new")).content.decode()

        assert 'id="route-delete"' not in body


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_delete_only_user_is_refused(client, bare_staff, route, method):
    _grant(bare_staff, "delete_pipelinedefinition")

    resp = getattr(client, method)(_url(route))

    assert resp.status_code == 403
    assert PipelineDefinition.objects.filter(pk=route.pk).exists()
