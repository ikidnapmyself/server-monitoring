import json
from unittest import mock

import pytest
from django.urls import reverse

from apps.console._tests.audit import written
from apps.console._tests.test_guard import visible
from apps.console._tests.views.test_channel import SECRET_VALUES
from apps.notify._tests.test_forms import STORED
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _empty_table():
    clear_lanes()


@pytest.fixture
def channel():
    return NotificationChannel.objects.create(name="ops", driver="slack", config=STORED["slack"])


def _url(channel):
    return reverse("console:channel-delete", args=[channel.pk])


def test_the_delete_url():
    assert reverse("console:channel-delete", args=[7]) == "/console/channels/7/delete/"


def test_anonymous_is_sent_to_the_console_login(client, channel):
    resp = client.get(_url(channel))

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("console:login"))


def test_an_anonymous_delete_logs_in_back_to_the_editor(client, channel):
    resp = client.post(_url(channel))

    assert resp.status_code == 302
    assert f"next=/console/channels/{channel.pk}/" in resp.url
    assert NotificationChannel.objects.filter(pk=channel.pk).exists()


def test_the_page_names_the_routes_it_cuts(client, superuser, channel):
    web = PipelineDefinition.objects.create(
        name="web", priority=10, stages=["notify"], channel=channel
    )
    pager = PipelineDefinition.objects.create(
        name="pager", priority=20, stages=["check", "notify"], channel=channel
    )
    PipelineDefinition.objects.create(name="quiet", priority=5, stages=["check"], channel=channel)

    resp = client.get(_url(channel))

    body = resp.content.decode()
    text = visible(body)
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/channel_delete.html"
    assert "<title>Delete channel · ops · Console</title>" in body
    assert "Delete channel ops?" in text
    assert "These routes will stop delivering:" in text
    assert f'<a id="channel-delete-lane-1" href="/console/routes/{web.pk}/">web</a>' in body
    assert f'<a id="channel-delete-lane-2" href="/console/routes/{pager.pk}/">pager</a>' in body
    assert "quiet" not in text
    form = body[body.index(f'<form method="post" action="{_url(channel)}"') :]
    assert "data-disable-on-submit" in form[:100]
    assert '<button id="channel-delete-confirm"' in form and "Delete channel</button>" in form
    assert (
        f'<a id="channel-delete-cancel" class="c-button" href="/console/channels/{channel.pk}/"'
        in form
    )
    for value in SECRET_VALUES:
        assert value not in body
    assert NotificationChannel.objects.filter(pk=channel.pk).exists()


def test_a_channel_no_route_delivers_through_says_so(client, superuser, channel):

    text = visible(client.get(_url(channel)).content.decode())

    assert "No route delivers through it." in text
    assert "These routes will stop delivering" not in text


def test_htmx_gets_the_page_without_the_layout(client, superuser, channel):

    resp = client.get(_url(channel), headers={"HX-Request": "true"})

    body = resp.content.decode()
    assert resp.templates[0].name == "console/partials/_channel_delete.html"
    assert "Delete channel ops?" in body
    assert "<html" not in body


def test_a_post_deletes_and_lands_on_the_channels_page(client, superuser, channel):
    lane = PipelineDefinition.objects.create(name="web", stages=["notify"], channel=channel)

    resp = client.post(_url(channel))

    assert resp.status_code == 303
    assert resp.url == reverse("console:channels")
    assert not NotificationChannel.objects.filter(pk=channel.pk).exists()
    lane.refresh_from_db()
    assert lane.channel is None
    assert lane.delivery_gap() == "no_channel"
    assert "Channel ops deleted." in client.get(resp.url).content.decode()


def test_a_superuser_deletes(client, superuser, channel):
    assert client.get(_url(channel)).status_code == 200
    assert client.post(_url(channel)).status_code == 303


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_channel_is_the_console_404_page(client, superuser, method):

    resp = getattr(client, method)(reverse("console:channel-delete", args=[999]))

    assert resp.status_code == 404
    assert "This channel doesn't exist. It may have been deleted." in resp.content.decode()


class TestTheEditorLink:
    def test_a_superuser_sees_it(self, client, superuser, channel):
        body = client.get(reverse("console:channel", args=[channel.pk])).content.decode()

        assert (
            f'<a id="channel-delete" class="c-button c-button--sm c-button--danger" '
            f'href="{_url(channel)}">Delete channel</a>'
        ) in body

    def test_a_new_channel_has_none(self, client, superuser):

        body = client.get(f"{reverse('console:channel-new')}?driver=slack").content.decode()

        assert 'id="channel-delete"' not in body


class TestAudit:
    def test_a_delete_is_recorded_with_its_secret_hidden(self, client, superuser, channel):
        pk = channel.pk

        client.post(_url(channel))

        entry = written().get()
        assert (entry.action, entry.target_kind) == ("channel.delete", "channel")
        assert (entry.target_id, entry.target_name) == (pk, "ops")
        assert entry.changes["name"] == {"old": "ops", "new": None}
        assert entry.changes["config.webhook_url"] == {"changed": True}
        text = json.dumps(entry.changes)
        for value in SECRET_VALUES:
            assert value not in text

    def test_a_missing_channel_records_nothing(self, client, superuser):
        client.post(reverse("console:channel-delete", args=[999]))

        assert not written().exists()

    def test_a_failed_record_rolls_the_delete_back(self, client, superuser, channel):
        with mock.patch("apps.console.views.channel_delete.record", side_effect=RuntimeError):
            with pytest.raises(RuntimeError):
                client.post(_url(channel))

        assert NotificationChannel.objects.filter(pk=channel.pk).exists()
