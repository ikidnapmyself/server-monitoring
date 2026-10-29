from unittest import mock

import pytest
from django.contrib.messages import get_messages
from django.db import IntegrityError, OperationalError
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries.keys import build_key_editor
from config.api_keys import StaleKey
from config.models import APIKey

pytestmark = pytest.mark.django_db

STALE = "This key changed since you opened it. Here it is now."


@pytest.fixture
def key():
    return APIKey.objects.create(name="hub", allowed_endpoints=["/alerts/webhook/"])


def _url(key=None):
    return reverse("console:key", args=[key.pk]) if key else reverse("console:key-new")


def _data(key=None, **overrides):
    """What the page posts back opened and saved untouched."""
    data = {"name": key.name if key else "", "allowed_endpoints": "", "digest": ""}
    if key is not None:
        data["allowed_endpoints"] = "\r\n".join(key.allowed_endpoints)
        data["digest"] = build_key_editor(key.pk).digest
        if key.is_active:
            data["is_active"] = "on"
    data.update(overrides)
    return data


def test_the_editor_urls():
    assert reverse("console:key-new") == "/console/keys/new/"
    assert reverse("console:key", args=[7]) == "/console/keys/7/"


def test_anonymous_is_sent_to_the_admin_login(client, key):
    resp = client.get(_url(key))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(key)}"


def test_an_anonymous_save_logs_in_back_to_the_editor(client, key):
    resp = client.post(_url(key), {})

    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(key)}"


def test_an_anonymous_add_logs_in_back_to_the_new_key_page(client):
    resp = client.post(_url(), {})

    assert resp["Location"] == f"{reverse('admin:login')}?next={_url()}"


def test_the_editor_renders_the_key(client, superuser, key):

    resp = client.get(_url(key))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/key.html"
    assert "<title>API key · hub · Console</title>" in body
    assert '<h1 class="c-pagehead__title">API key hub</h1>' in body
    assert f'action="/console/keys/{key.pk}/"' in body
    assert 'name="digest" value="' in body
    assert 'name="name" value="hub"' in body
    assert 'aria-describedby="id_is_active_helptext" id="id_is_active" checked' in body
    assert "/alerts/webhook/</textarea>" in body
    assert body.index('name="name"') < body.index('name="is_active"')
    assert body.index('name="is_active"') < body.index('name="allowed_endpoints"')
    assert "data-disable-on-submit" in body
    assert f'<a id="key-back" class="c-crumb" href="/console/keys/#key-{key.pk}"' in body
    details = body[body.index('id="key-details"') :]
    assert f"<dt>Prefix</dt><dd>{key.prefix}…</dd>" in details
    assert "<dt>Created</dt>" in details and "<dt>Last used</dt><dd>Never</dd>" in details
    assert key.key not in body
    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_htmx_gets_the_editor_without_the_layout(client, superuser, key):

    resp = client.get(_url(key), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_key.html"
    assert "<html" not in resp.content.decode()


def test_the_editor_opens_enabled(client, superuser, key):
    assert 'id="key-save"' in client.get(_url(key)).content.decode()


def test_a_save_writes_and_lands_with_a_message(client, superuser, key):

    resp = client.post(_url(key), _data(key, name="hub-2", is_active="", allowed_endpoints=""))

    assert resp.status_code == 303
    assert resp["Location"] == f"/console/keys/{key.pk}/"
    key.refresh_from_db()
    assert (key.name, key.is_active, key.allowed_endpoints) == ("hub-2", False, [])
    assert "Key saved." in client.get(resp["Location"]).content.decode()


def test_an_invalid_save_re_renders(client, superuser, key):

    resp = client.post(_url(key), _data(key, allowed_endpoints="alerts", digest="kept"))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "Each line must be a path starting with /." in body
    assert 'name="digest" value="kept"' in body
    key.refresh_from_db()
    assert key.allowed_endpoints == ["/alerts/webhook/"]


def test_a_stale_save_is_a_409_with_the_fresh_values(client, superuser, key):
    opened = _data(key, name="mine")
    APIKey.objects.filter(pk=key.pk).update(name="theirs")

    resp = client.post(_url(key), opened)

    body = resp.content.decode()
    assert resp.status_code == 409
    assert STALE in body
    assert 'name="name" value="theirs"' in body
    key.refresh_from_db()
    assert key.name == "theirs"


@pytest.mark.parametrize("error", [OperationalError("locked"), IntegrityError("constraint")])
def test_a_busy_database_is_a_409(client, superuser, key, error):
    with mock.patch("apps.console.views.key.save_key", side_effect=error):
        resp = client.post(_url(key), _data(key, name="x"))

    assert resp.status_code == 409
    assert "Couldn&#x27;t save just now. Here&#x27;s the key as it is." in resp.content.decode()


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_key_is_the_console_404_page(client, superuser, method):

    resp = getattr(client, method)(reverse("console:key", args=[999]))

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This API key doesn't exist. It may have been deleted." in body
    assert '<a id="not-found-back" href="/console/keys/">Back to API keys</a>' in body


def test_a_key_deleted_during_the_save_answers_the_404(client, superuser, key):
    editors = [build_key_editor(key.pk, _data(key)), None]

    with (
        mock.patch("apps.console.views.key.build_key_editor", side_effect=editors),
        mock.patch("apps.console.views.key.save_key", side_effect=StaleKey),
    ):
        resp = client.post(_url(key), _data(key))

    assert resp.status_code == 404


class TestANewKey:
    def test_it_opens_an_empty_form(self, client, superuser):
        resp = client.get(_url())

        body = resp.content.decode()
        assert resp.status_code == 200
        assert "<title>New API key · Console</title>" in body
        assert '<h1 class="c-pagehead__title">New API key</h1>' in body
        assert 'action="/console/keys/new/"' in body
        assert 'aria-describedby="id_is_active_helptext" id="id_is_active" checked' in body
        assert ">Create key</button>" in body
        assert 'id="key-details"' not in body

    def test_creating_shows_the_token_once(self, client, superuser):
        resp = client.post(_url(), _data(name="agent", is_active="on", allowed_endpoints="/a/"))

        key = APIKey.objects.get()
        body = resp.content.decode()
        token = resp.context["token"]
        assert resp.status_code == 200
        assert resp.templates[0].name == "console/key_created.html"
        assert "no-store" in resp["Cache-Control"]
        assert len(token) == 40 and token[:8] == key.prefix
        assert f'value="{token}" readonly' in body
        assert "Copy this key now. It isn't shown again." in body
        assert (
            '<p class="c-field__help">Set it as HUB_API_KEY on each node that pushes here. '
            "Webhooks send it as Authorization: Bearer or in the X-API-Key header.</p>"
        ) in body
        assert "Nodes send it in the X-API-Key header" not in body
        assert "console/partials/_messages.html" in [t.name for t in resp.templates]
        assert (
            f'<a id="key-created-done" class="c-button c-button--primary" href="/console/keys/{key.pk}/">'
            in body
        )
        assert (key.name, key.is_active, key.allowed_endpoints) == ("agent", True, ["/a/"])
        assert key.key not in body
        assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]

        assert all(token not in str(m) for m in get_messages(resp.wsgi_request))
        assert all(token not in morsel.value for morsel in resp.cookies.values())
        assert all(token not in morsel.value for morsel in client.cookies.values())
        for later in (reverse("console:keys"), _url(key)):
            assert token not in client.get(later).content.decode()

    def test_a_double_create_makes_two_keys(self, client, superuser):
        data = _data(name="agent", is_active="on")

        client.post(_url(), data)
        client.post(_url(), data)

        assert APIKey.objects.filter(name="agent").count() == 2

    def test_an_invalid_one_saves_nothing(self, client, superuser):
        resp = client.post(_url(), _data(name=""))

        assert resp.status_code == 200
        assert "This field is required." in resp.content.decode()
        assert not APIKey.objects.exists()

    @pytest.mark.parametrize("error", [IntegrityError("x"), OperationalError("locked")])
    def test_a_failed_create_is_a_409_that_saves_nothing(self, client, superuser, error):
        with mock.patch("apps.console.views.key.APIKey.objects.create", side_effect=error):
            resp = client.post(_url(), _data(name="agent"))

        body = resp.content.decode()
        assert resp.status_code == 409
        assert "Couldn&#x27;t add the key just now." in body
        assert 'name="name" value="agent"' in body
        assert not APIKey.objects.exists()
