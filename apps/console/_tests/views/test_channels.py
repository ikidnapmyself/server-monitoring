from unittest import mock

import pytest
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def _empty_table():
    clear_lanes()


@pytest.fixture
def url():
    return reverse("console:channels")


def _channel(name, driver="slack", **kw):
    return NotificationChannel.objects.create(name=name, driver=driver, **kw)


def test_the_page_lives_at_channels(url):
    assert url == "/console/channels/"


def test_anonymous_is_sent_to_the_admin_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/channels/"


def test_channels_render_by_name_with_driver_state_and_lanes(client, superuser, url):
    ops = _channel("ops")
    _channel("mail", driver="email", is_active=False)
    lane = PipelineDefinition.objects.create(name="web", stages=["notify"], channel=ops)

    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/channels.html"
    assert "<title>Channels · Console</title>" in body
    assert body.index(">mail</a>") < body.index(">ops</a>")
    assert f'<a id="channel-{ops.pk}-edit" href="/console/channels/{ops.pk}/">ops</a>' in body
    assert '<span class="c-label">Slack</span>' in body
    assert '<span class="c-label c-tone-muted">Inactive</span>' in body
    assert '<span class="c-label c-tone-ok">Active</span>' in body
    assert f'href="/console/routes/{lane.pk}/">web</a>' in body
    assert "No route sends here" in body
    assert (
        '<a id="channels-add" class="c-button c-button--sm c-button--primary" '
        'href="/console/channels/new/">Add a channel</a>'
    ) in body
    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_a_lane_that_cant_deliver_says_why(client, superuser, url):
    pigeon = _channel("pigeon", driver="carrier_pigeon")
    PipelineDefinition.objects.create(name="pager", stages=["notify"], channel=pigeon)

    body = client.get(url).content.decode()

    assert (
        '<div class="c-row__meta c-tone-critical">pager can\'t deliver: '
        "the channel uses a driver this hub doesn&#x27;t have</div>"
    ) in body
    assert '<span class="c-row__icon c-tone-critical">' in body
    assert "A driver this hub doesn" in body


def test_the_page_sits_under_system_with_its_subnav(client, superuser, url):

    body = client.get(url).content.decode()

    assert 'href="/console/channels/" aria-current="page">Channels</a>' in body


def test_a_name_links_the_channel(client, superuser, url):
    ops = _channel("ops")

    body = client.get(url).content.decode()

    assert f'<a id="channel-{ops.pk}-edit" href="/console/channels/{ops.pk}/">ops</a>' in body


def test_a_name_without_a_link_is_plain_text(client, superuser, url):
    _channel("ops")

    with mock.patch("apps.console.intents.INTENTS", {}):
        body = client.get(url).content.decode()

    assert "<strong>ops</strong>" in body


def test_q_filters_by_name(client, superuser, url):
    _channel("ops-slack")
    _channel("mail", driver="email")

    body = client.get(url, {"q": " OPS\x00 "}).content.decode()

    assert ">ops-slack</a>" in body
    assert ">mail</a>" not in body
    assert 'name="q" value="OPS"' in body


def test_a_search_matching_nothing_offers_to_clear_it(client, superuser, url):
    _channel("ops")

    body = client.get(url, {"q": "zzz"}).content.decode()

    assert "No channels match this search." in body
    assert '<a id="channels-clear" href="/console/channels/"' in body


def test_no_channels_says_what_that_means(client, superuser, url):

    body = client.get(url).content.decode()

    assert "No channels." in body
    assert "Until one exists, no route can deliver." in body


def test_htmx_gets_the_list_without_the_layout(client, superuser, url):

    resp = client.get(url, **HTMX)

    assert resp.templates[0].name == "console/partials/_channels.html"
    assert "<html" not in resp.content.decode()
