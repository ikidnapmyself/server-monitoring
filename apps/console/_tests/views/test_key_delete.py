from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.console._tests.test_guard import visible
from config.models import APIKey

pytestmark = pytest.mark.django_db

REFUSED = "Anything still sending this key gets refused."


@pytest.fixture
def key():
    return APIKey.objects.create(name="agent")


def _url(key):
    return reverse("console:key-delete", args=[key.pk])


def test_the_delete_url():
    assert reverse("console:key-delete", args=[7]) == "/console/keys/7/delete/"


def test_anonymous_is_sent_to_the_console_login(client, key):
    resp = client.get(_url(key))

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("console:login"))


def test_an_anonymous_delete_logs_in_back_to_the_editor(client, key):
    resp = client.post(_url(key))

    assert resp.status_code == 302
    assert f"next=/console/keys/{key.pk}/" in resp.url
    assert APIKey.objects.filter(pk=key.pk).exists()


def test_the_page_says_a_used_key_gets_refused_and_when_it_was_used(client, superuser, key):
    APIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now() - timedelta(days=2))

    resp = client.get(_url(key))

    body = resp.content.decode()
    text = " ".join(visible(body).split())
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/key_delete.html"
    assert "<title>Delete API key · agent · Console</title>" in body
    assert "Delete API key agent?" in text
    assert f"{REFUSED} It was last used 2 days ago ." in text.replace("\xa0", " ")
    form = body[body.index(f'<form method="post" action="{_url(key)}"') :]
    assert "data-disable-on-submit" in form[:100]
    assert '<button id="key-delete-confirm"' in form and "Delete key</button>" in form
    assert f'<a id="key-delete-cancel" class="c-button" href="/console/keys/{key.pk}/"' in form
    assert key.key not in body
    assert 'href="/console/keys/" aria-current="page">API keys</a>' in body


def test_a_key_never_used_says_so(client, superuser, key):

    text = visible(client.get(_url(key)).content.decode())

    assert f"{REFUSED} It was never used." in " ".join(text.split())


def test_htmx_gets_the_page_without_the_layout(client, superuser, key):

    resp = client.get(_url(key), headers={"HX-Request": "true"})

    assert resp.templates[0].name == "console/partials/_key_delete.html"
    assert "<html" not in resp.content.decode()


def test_a_post_deletes_and_lands_on_the_keys_page(client, superuser, key):

    resp = client.post(_url(key))

    assert resp.status_code == 303
    assert resp.url == reverse("console:keys")
    assert not APIKey.objects.filter(pk=key.pk).exists()
    assert "API key agent deleted." in client.get(resp.url).content.decode()


def test_a_superuser_deletes(client, superuser, key):
    assert client.get(_url(key)).status_code == 200
    assert client.post(_url(key)).status_code == 303


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_key_is_the_console_404_page(client, superuser, method):

    resp = getattr(client, method)(reverse("console:key-delete", args=[999]))

    assert resp.status_code == 404
    assert "This API key doesn't exist. It may have been deleted." in resp.content.decode()


class TestTheEditorLink:
    def test_a_superuser_sees_it(self, client, superuser, key):
        body = client.get(reverse("console:key", args=[key.pk])).content.decode()

        assert (
            f'<a id="key-delete" class="c-button c-button--sm c-button--danger" '
            f'href="{_url(key)}">Delete key</a>'
        ) in body

    def test_a_new_key_has_none(self, client, superuser):

        assert 'id="key-delete"' not in client.get(reverse("console:key-new")).content.decode()
