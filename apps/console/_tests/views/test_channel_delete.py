import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

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


def _grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="notify", codename=codename)
        )
    return user


@pytest.fixture
def channel_editor(bare_staff):
    return _grant(bare_staff, "change_notificationchannel")


@pytest.fixture
def channel_deleter(bare_staff):
    return _grant(bare_staff, "change_notificationchannel", "delete_notificationchannel")


def _url(channel):
    return reverse("console:channel-delete", args=[channel.pk])


def test_the_delete_url():
    assert reverse("console:channel-delete", args=[7]) == "/console/channels/7/delete/"


def test_anonymous_is_sent_to_the_admin_login(client, channel):
    resp = client.get(_url(channel))

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("admin:login"))


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
    client.force_login(superuser)

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


def test_a_reader_without_route_access_sees_the_routes_as_text(client, channel_deleter, channel):
    PipelineDefinition.objects.create(name="web", stages=["notify"], channel=channel)

    body = client.get(_url(channel)).content.decode()

    assert "<li>web</li>" in body


def test_a_channel_no_route_delivers_through_says_so(client, superuser, channel):
    client.force_login(superuser)

    text = visible(client.get(_url(channel)).content.decode())

    assert "No route delivers through it." in text
    assert "These routes will stop delivering" not in text


def test_htmx_gets_the_page_without_the_layout(client, superuser, channel):
    client.force_login(superuser)

    resp = client.get(_url(channel), headers={"HX-Request": "true"})

    body = resp.content.decode()
    assert resp.templates[0].name == "console/partials/_channel_delete.html"
    assert "Delete channel ops?" in body
    assert "<html" not in body


def test_a_post_deletes_and_lands_on_the_channels_page(client, superuser, channel):
    lane = PipelineDefinition.objects.create(name="web", stages=["notify"], channel=channel)
    client.force_login(superuser)

    resp = client.post(_url(channel))

    assert resp.status_code == 303
    assert resp.url == reverse("console:channels")
    assert not NotificationChannel.objects.filter(pk=channel.pk).exists()
    lane.refresh_from_db()
    assert lane.channel is None
    assert lane.delivery_gap() == "no_channel"
    assert "Channel ops deleted." in client.get(resp.url).content.decode()


def test_a_delete_permission_is_enough(client, channel_deleter, channel):
    assert client.get(_url(channel)).status_code == 200
    assert client.post(_url(channel)).status_code == 303


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_change_only_user_is_refused(client, channel_editor, channel, method):
    resp = getattr(client, method)(_url(channel))

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to delete channels required."
    assert NotificationChannel.objects.filter(pk=channel.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_channel_is_the_console_404_page(client, superuser, method):
    client.force_login(superuser)

    resp = getattr(client, method)(reverse("console:channel-delete", args=[999]))

    assert resp.status_code == 404
    assert "This channel doesn't exist. It may have been deleted." in resp.content.decode()


class TestTheEditorLink:
    def test_a_deleter_sees_it(self, client, channel_deleter, channel):
        body = client.get(reverse("console:channel", args=[channel.pk])).content.decode()

        assert (
            f'<a id="channel-delete" class="c-button c-button--sm c-button--danger" '
            f'href="{_url(channel)}">Delete channel</a>'
        ) in body

    def test_a_change_only_user_does_not(self, client, channel_editor, channel):
        body = client.get(reverse("console:channel", args=[channel.pk])).content.decode()

        assert 'id="channel-delete"' not in body

    def test_a_new_channel_has_none(self, client, superuser):
        client.force_login(superuser)

        body = client.get(f"{reverse('console:channel-new')}?driver=slack").content.decode()

        assert 'id="channel-delete"' not in body
