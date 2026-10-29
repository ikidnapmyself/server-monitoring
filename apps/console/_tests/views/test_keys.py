from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.console._tests.test_templates import FORBIDDEN
from config.models import APIKey

pytestmark = pytest.mark.django_db


@pytest.fixture
def url():
    return reverse("console:keys")


def test_the_page_lives_at_keys(url):
    assert url == "/console/keys/"


def test_anonymous_is_sent_to_the_admin_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/keys/"


def test_keys_render_by_name_with_state_prefix_endpoints_and_use(client, superuser, url):
    hub = APIKey.objects.create(
        name="hub",
        allowed_endpoints=["/alerts/webhook/"],
        last_used_at=timezone.now() - timedelta(hours=3),
    )
    old = APIKey.objects.create(name="agent", is_active=False)

    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/keys.html"
    assert "<title>API keys · Console</title>" in body
    assert body.index(">agent</a>") < body.index(">hub</a>")
    assert f'<a id="key-{hub.pk}-edit" href="/console/keys/{hub.pk}/">hub</a>' in body
    assert '<span class="c-label c-tone-ok">On</span>' in body
    assert '<span class="c-label c-tone-muted">Off</span>' in body
    assert f'<span class="c-mono">{hub.prefix}…</span> · /alerts/webhook/ · used <time' in body
    assert "3\xa0hours ago</time>" in body
    assert f'<span class="c-mono">{old.prefix}…</span> · All endpoints · never used' in body
    assert hub.key not in body and old.key not in body
    assert (
        '<a id="keys-add" class="c-button c-button--sm c-button--primary" '
        'href="/console/keys/new/">Add a key</a>'
    ) in body
    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_no_keys_says_nothing_can_send(client, superuser, url):

    body = client.get(url).content.decode()

    assert "No API keys." in body
    assert "Until one exists, nodes and webhooks can&#x27;t send to this hub." not in body
    assert "Until one exists, nodes and webhooks can't send to this hub." in body


@override_settings(API_KEY_AUTH_ENABLED=False)
def test_no_keys_with_checks_off_says_anything_can_send(client, superuser, url):

    body = client.get(url).content.decode()

    assert "No API keys." in body
    assert "Key checks are off, so anything can send to this hub." in body
    assert "can't send to this hub" not in body


def test_a_name_links_the_key(client, superuser, url):
    hub = APIKey.objects.create(name="hub")

    body = client.get(url).content.decode()

    assert f'href="/console/keys/{hub.pk}/">hub</a>' in body


def test_a_name_without_a_link_is_plain_text(client, superuser, url):
    APIKey.objects.create(name="hub")

    with mock.patch("apps.console.intents.INTENTS", {}):
        body = client.get(url).content.decode()

    assert "<strong>hub</strong>" in body


def test_htmx_gets_the_list_without_the_layout(client, superuser, url):

    resp = client.get(url, HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_keys.html"
    assert "<html" not in resp.content.decode()
