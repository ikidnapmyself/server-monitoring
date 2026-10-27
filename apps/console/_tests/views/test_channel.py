from unittest import mock

import pytest
from django.db import IntegrityError, OperationalError
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries.channels import build_channel_editor
from apps.notify._tests.test_forms import PD_KEY, SLACK_URL, STORED, initial_data
from apps.notify.channels import StaleChannel
from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.forms import ChannelForm
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db

STALE = "This channel changed since you opened it. Here it is now."
UNKNOWN_VALUE = "unknown-key-value-123"


@pytest.fixture(autouse=True)
def _empty_table():
    clear_lanes()


@pytest.fixture
def channel():
    return NotificationChannel.objects.create(
        name="ops", driver="slack", config={**STORED["slack"], "legacy": UNKNOWN_VALUE}
    )


def _url(channel=None, driver=None):
    if channel is None:
        url = reverse("console:channel-new")
    else:
        url = reverse("console:channel", args=[channel.pk])
    return f"{url}?driver={driver}" if driver else url


def _data(channel=None, start="slack", **overrides):
    """What the page posts back opened and saved untouched."""
    form = ChannelForm(channel=channel, driver=None if channel else start)
    data = initial_data(form)
    if channel is not None:
        data["digest"] = build_channel_editor(channel.pk, can_change=True).digest
    data.update(overrides)
    return data


SECRET_VALUES = (
    SLACK_URL,
    "hunter2-password",
    PD_KEY,
    "hook-endpoint-secret",
    "header-secret-value",
    "alias-secret-value",
)


def _secrets_absent(body, *extra):
    for value in (*SECRET_VALUES, *extra):
        assert value not in body


def test_the_editor_urls():
    assert reverse("console:channel-new") == "/console/channels/new/"
    assert reverse("console:channel", args=[7]) == "/console/channels/7/"


def test_anonymous_is_sent_to_the_admin_login(client, channel):
    resp = client.get(_url(channel))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(channel)}"


def test_an_anonymous_save_logs_in_back_to_the_editor(client, channel):
    resp = client.post(_url(channel), {})

    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(channel)}"


def test_an_anonymous_add_logs_in_back_to_the_new_channel_page(client):
    resp = client.post(_url(driver="slack"), {})

    assert resp["Location"] == f"{reverse('admin:login')}?next={_url()}"


def test_the_editor_renders_the_channel(client, superuser, channel):

    resp = client.get(_url(channel))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/channel.html"
    assert "<title>Channel · ops · Console</title>" in body
    assert '<h1 class="c-pagehead__title">Channel ops</h1>' in body
    assert f'action="/console/channels/{channel.pk}/?driver=slack"' in body
    assert 'name="digest" value="' in body
    assert 'name="name" value="ops"' in body
    assert '<option value="slack" selected>Slack</option>' in body
    assert 'name="switch_driver"' in body and "Show this driver's settings" in body
    assert body.index('id="channel-default"') < body.index('id="channel-switch-driver"')
    assert body.index('id="channel-switch-driver"') < body.index('id="channel-save"')
    assert 'type="password" name="cfg__webhook_url"' in body
    assert "Set. Leave blank to keep it." in body
    assert "data-disable-on-submit" in body
    assert (
        f'<a id="channel-back" class="c-crumb" href="/console/channels/#channel-{channel.pk}"'
        in (body)
    )
    assert f'href="/console/channels/{channel.pk}/delete/">Delete channel</a>' in body
    assert "data-poll" not in body[body.index("<main") :]


def test_the_editor_reads_in_a_fixed_number_of_queries(
    client, superuser, channel, django_assert_num_queries
):
    for i in range(3):
        PipelineDefinition.objects.create(name=f"lane-{i}", stages=["notify"], channel=channel)
    client.get(_url(channel))

    with django_assert_num_queries(4):
        assert client.get(_url(channel)).status_code == 200


def test_the_message_templates_sit_in_details(client, superuser, channel):

    body = client.get(_url(channel)).content.decode()

    templates = body[body.index('<details id="channel-templates"') :]
    assert templates.index("data-details") < templates.index(">")
    assert '<summary id="channel-templates-summary">Message templates</summary>' in templates
    assert 'name="cfg__template"' in templates
    assert 'name="cfg__template"' not in body[: body.index('<details id="channel-templates"')]


def test_no_secret_or_unknown_value_reaches_the_page(client, superuser, channel):

    body = client.get(_url(channel)).content.decode()

    _secrets_absent(body, UNKNOWN_VALUE)
    assert "Saving keeps the settings this driver doesn't declare." in body


@pytest.mark.parametrize("driver", sorted(STORED))
def test_no_driver_shows_its_secrets(client, superuser, driver):
    ch = NotificationChannel.objects.create(name="x", driver=driver, config=STORED[driver])

    body = client.get(_url(ch)).content.decode()

    _secrets_absent(body)


def test_the_details_hold_the_facts(client, superuser, channel):

    body = client.get(_url(channel)).content.decode()

    details = body[body.index('id="channel-details"') :]
    assert "<dt>Driver</dt><dd>slack</dd>" in details
    assert "<dt>Settings this driver doesn&#x27;t declare</dt><dd>legacy</dd>" in details
    assert "<dt>Created</dt>" in details and "<dt>Updated</dt>" in details


def test_routed_here_by_lists_each_lane_with_its_gap(client, superuser, channel):
    NotificationChannel.objects.filter(pk=channel.pk).update(is_active=False)
    lane = PipelineDefinition.objects.create(name="web", stages=["notify"], channel=channel)

    body = client.get(_url(channel)).content.decode()

    lanes = body[body.index('id="channel-lanes"') :]
    assert "Routed here by" in lanes
    assert f'<a id="channel-lane-1" href="/console/routes/{lane.pk}/">web</a>' in lanes
    assert (
        '<div class="c-row__meta c-tone-critical">'
        "Can't deliver: the matched route has no active channel</div>"
    ) in lanes
    assert '<span class="c-row__icon c-tone-critical">' in lanes
    assert '<textarea name="description" cols="40" rows="3"' in body
    assert '<div class="c-check"><input type="checkbox" name="is_active"' in body


def test_a_channel_no_route_uses_says_so(client, superuser, channel):

    body = client.get(_url(channel)).content.decode()

    assert "No route sends here." in body


def test_htmx_gets_the_editor_without_the_layout(client, superuser, channel):

    resp = client.get(_url(channel), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_channel.html"
    assert "<html" not in resp.content.decode()


def test_the_page_carries_nothing_the_csp_blocks(client, superuser, channel):

    body = client.get(_url(channel)).content.decode()

    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_the_editor_opens_enabled(client, superuser, channel):
    body = client.get(_url(channel)).content.decode()

    assert 'id="channel-save"' in body


def test_an_untouched_save_keeps_the_config(client, superuser, channel):
    stored = dict(channel.config)

    resp = client.post(_url(channel), _data(channel, name="ops-2"))

    assert resp.status_code == 303
    assert resp["Location"] == f"/console/channels/{channel.pk}/"
    channel.refresh_from_db()
    assert (channel.name, channel.config) == ("ops-2", stored)
    assert "Channel saved." in client.get(resp["Location"]).content.decode()


def test_a_new_secret_is_written(client, superuser, channel):
    new = "https://hooks.slack.com/services/T1/B1/new"

    client.post(_url(channel), _data(channel, cfg__webhook_url=new))

    channel.refresh_from_db()
    assert channel.config["webhook_url"] == new


def test_an_invalid_save_re_renders_without_secrets(client, superuser, channel):
    typed = "https://hooks.slack.com/services/T9/B9/typed-secret"

    resp = client.post(
        _url(channel), _data(channel, name="", cfg__webhook_url=typed, digest="kept")
    )

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "This field is required." in body
    assert 'name="digest" value="kept"' in body
    _secrets_absent(body, UNKNOWN_VALUE, typed)
    channel.refresh_from_db()
    assert channel.name == "ops"


def test_a_stale_save_is_a_409_with_the_fresh_values(client, superuser, channel):
    opened = _data(channel, description="mine")
    NotificationChannel.objects.filter(pk=channel.pk).update(description="theirs")

    resp = client.post(_url(channel), opened)

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "This channel changed since you opened it. Here it is now." in body
    assert "theirs</textarea>" in body
    _secrets_absent(body, UNKNOWN_VALUE)
    channel.refresh_from_db()
    assert channel.description == "theirs"


def test_a_busy_database_is_a_409(client, superuser, channel):
    with mock.patch(
        "apps.console.views.channel.save_channel",
        side_effect=OperationalError("database is locked"),
    ):
        resp = client.post(_url(channel), _data(channel, description="x"))

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "Couldn&#x27;t save just now. Here&#x27;s the channel as it is." in body


def test_a_name_another_channel_has_is_refused(client, superuser, channel):
    NotificationChannel.objects.create(name="db", driver="email")

    resp = client.post(_url(channel), _data(channel, name="db"))

    assert resp.status_code == 200
    assert "Another channel already has this name." in resp.content.decode()


def test_a_name_taken_in_a_race_is_a_name_error(client, superuser, channel):
    NotificationChannel.objects.create(name="db", driver="email")

    with mock.patch("apps.notify.forms.ChannelForm.clean_name", lambda self: "db"):
        resp = client.post(_url(channel), _data(channel, name="db"))

    assert resp.status_code == 200
    assert "Another channel already has this name." in resp.content.decode()
    channel.refresh_from_db()
    assert channel.name == "ops"


def test_an_integrity_error_without_a_clashing_name_is_a_stale_save(client, superuser, channel):
    with mock.patch(
        "apps.console.views.channel.save_channel", side_effect=IntegrityError("constraint")
    ):
        resp = client.post(_url(channel), _data(channel, name="db"))

    body = resp.content.decode()
    assert resp.status_code == 409
    assert STALE in body
    assert "Another channel already has this name." not in body


class TestSwitchingDriver:
    def test_it_re_renders_for_the_new_driver_and_saves_nothing(self, client, superuser, channel):
        data = _data(channel, name="renamed", driver="email", switch_driver="1")
        data["cfg__webhook_url"] = "garbage"

        resp = client.post(_url(channel), data)

        body = resp.content.decode()
        assert resp.status_code == 200
        assert f'action="/console/channels/{channel.pk}/?driver=email"' in body
        assert 'name="name" value="renamed"' in body
        assert '<option value="email" selected>Email</option>' in body
        assert 'name="cfg__smtp_host"' in body
        assert 'name="cfg__webhook_url"' not in body
        assert 'name="cfg__use_tls" id="id_cfg__use_tls" checked' in body
        assert "c-field__error" not in body
        _secrets_absent(body, UNKNOWN_VALUE)
        channel.refresh_from_db()
        assert (channel.name, channel.driver) == ("ops", "slack")

    def test_the_switched_form_saves_on_the_new_driver(self, client, superuser, channel):
        switched = client.post(_url(channel), _data(channel, driver="generic", switch_driver="1"))
        form = switched.context["editor"].form
        data = initial_data(form)
        data.update(digest=switched.context["editor"].digest, cfg__endpoint="https://e.example/")

        resp = client.post(_url(channel, driver="generic"), data)

        assert resp.status_code == 303
        channel.refresh_from_db()
        assert channel.driver == "generic"
        assert channel.config["endpoint"] == "https://e.example/"
        assert "webhook_url" not in channel.config

    def test_on_the_driver_shown_it_keeps_the_edits(self, client, superuser, channel):

        resp = client.post(
            _url(channel, driver="slack"),
            _data(channel, name="", cfg__timeout="7", switch_driver="1"),
        )

        body = resp.content.decode()
        assert resp.status_code == 200
        assert 'name="cfg__timeout" value="7"' in body
        assert "c-field__error" not in body and "aria-invalid" not in body
        channel.refresh_from_db()
        assert channel.name == "ops"

    def test_an_unknown_driver_is_a_400(self, client, superuser, channel):

        resp = client.post(_url(channel), _data(channel, driver="nope", switch_driver="1"))

        body = resp.content.decode()
        assert resp.status_code == 400
        assert "This hub has no driver by that name. Pick one it has." in body
        assert '<option value="slack" selected>Slack</option>' in body
        _secrets_absent(body, UNKNOWN_VALUE)
        channel.refresh_from_db()
        assert channel.driver == "slack"

    def test_a_switch_on_a_deleted_channel_is_the_404(self, client, superuser, channel):
        url, data = _url(channel), _data(channel, switch_driver="1")
        channel.delete()

        assert client.post(url, data).status_code == 404

    def test_a_same_driver_switch_on_a_deleted_channel_is_the_404(self, client, superuser, channel):
        url, data = _url(channel, driver="slack"), _data(channel, switch_driver="1")
        channel.delete()

        assert client.post(url, data).status_code == 404


def test_an_unknown_driver_in_the_url_is_a_400(client, superuser, channel):

    resp = client.get(_url(channel, driver="nope"))

    body = resp.content.decode()
    assert resp.status_code == 400
    assert "This hub has no driver by that name." in body
    _secrets_absent(body, UNKNOWN_VALUE)


def test_an_unknown_driver_on_a_save_saves_nothing(client, superuser, channel):

    resp = client.post(_url(channel, driver="nope"), _data(channel, name="renamed"))

    assert resp.status_code == 400
    _secrets_absent(resp.content.decode(), UNKNOWN_VALUE)
    channel.refresh_from_db()
    assert channel.name == "ops"


class TestAChannelOnADriverThisHubDoesntHave:
    @pytest.fixture
    def pigeon(self):
        return NotificationChannel.objects.create(
            name="pigeon", driver="carrier_pigeon", config={"coop": UNKNOWN_VALUE}
        )

    def test_it_renders_with_no_typed_fields(self, client, superuser, pigeon):

        body = client.get(_url(pigeon)).content.decode()

        assert '<option value="carrier_pigeon" selected>A driver this hub doesn' in body
        assert 'name="cfg__' not in body
        assert 'id="channel-templates"' not in body
        assert "<dt>Settings this driver doesn&#x27;t declare</dt><dd>coop</dd>" in body
        _secrets_absent(body, UNKNOWN_VALUE)

    def test_saving_keeps_its_config(self, client, superuser, pigeon):

        resp = client.post(_url(pigeon), _data(pigeon, description="kept"))

        assert resp.status_code == 303
        pigeon.refresh_from_db()
        assert (pigeon.config, pigeon.description) == ({"coop": UNKNOWN_VALUE}, "kept")


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_channel_is_the_console_404_page(client, superuser, method):

    resp = getattr(client, method)(reverse("console:channel", args=[999]))

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This channel doesn't exist. It may have been deleted." in body
    assert '<a id="not-found-back" href="/console/channels/">Back to channels</a>' in body


def test_a_bad_driver_on_a_missing_channel_is_the_404(client, superuser):

    assert client.get(_url(None) + "999/?driver=nope").status_code == 404


def test_a_channel_deleted_during_the_save_answers_the_404(client, superuser, channel):
    editors = [build_channel_editor(channel.pk, _data(channel), can_change=True), None]

    with (
        mock.patch("apps.console.views.channel.build_channel_editor", side_effect=editors),
        mock.patch("apps.console.views.channel.save_channel", side_effect=StaleChannel),
    ):
        resp = client.post(_url(channel), _data(channel))

    assert resp.status_code == 404


class TestANewChannel:
    def test_without_a_driver_it_lists_the_drivers(self, client, superuser):
        resp = client.get(_url())

        body = resp.content.decode()
        assert resp.status_code == 200
        assert resp.templates[0].name == "console/channel_new.html"
        assert "<title>New channel · Console</title>" in body
        for key, driver_cls in DRIVER_REGISTRY.items():
            assert f'href="/console/channels/new/?driver={key}">{driver_cls.label}</a>' in body
        assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]

    def test_a_driver_opens_its_form(self, client, superuser):
        resp = client.get(_url(driver="email"))

        body = resp.content.decode()
        assert resp.status_code == 200
        assert '<h1 class="c-pagehead__title">New channel</h1>' in body
        assert 'action="/console/channels/new/?driver=email"' in body
        assert 'name="cfg__smtp_host"' in body
        assert 'id="channel-details"' not in body
        assert 'id="channel-lanes"' not in body

    def test_an_unknown_driver_is_a_400_with_the_list(self, client, superuser):
        resp = client.get(_url(driver="nope"))

        body = resp.content.decode()
        assert resp.status_code == 400
        assert "This hub has no driver by that name. Pick one it has." in body
        assert 'href="/console/channels/new/?driver=slack"' in body

    def test_a_save_without_a_driver_is_a_400(self, client, superuser):
        resp = client.post(_url(), _data(name="db"))

        assert resp.status_code == 400
        assert not NotificationChannel.objects.exists()

    def test_a_save_creates_it(self, client, superuser):
        resp = client.post(
            _url(driver="slack"), _data(name="db", cfg__webhook_url=SLACK_URL, description="d")
        )

        ch = NotificationChannel.objects.get(name="db")
        assert resp.status_code == 303
        assert resp["Location"] == f"/console/channels/{ch.pk}/"
        assert (ch.driver, ch.description, ch.is_active) == ("slack", "d", True)
        assert ch.config == {"webhook_url": SLACK_URL}

    def test_an_invalid_one_saves_nothing(self, client, superuser):
        resp = client.post(_url(driver="slack"), _data(name="db"))

        assert resp.status_code == 200
        assert "Webhook URL is required." in resp.content.decode()
        assert not NotificationChannel.objects.exists()

    def test_a_switch_saves_nothing(self, client, superuser):
        resp = client.post(
            _url(driver="slack"), _data(name="db", driver="email", switch_driver="1")
        )

        body = resp.content.decode()
        assert resp.status_code == 200
        assert 'action="/console/channels/new/?driver=email"' in body
        assert 'name="name" value="db"' in body
        assert not NotificationChannel.objects.exists()

    def test_an_unknown_switch_is_a_400_with_the_list(self, client, superuser):
        resp = client.post(_url(driver="slack"), _data(driver="nope", switch_driver="1"))

        assert resp.status_code == 400
        assert resp.templates[0].name == "console/channel_new.html"

    def test_a_name_taken_in_a_race_is_a_name_error(self, client, superuser):
        NotificationChannel.objects.create(name="db", driver="email")
        with (
            mock.patch("apps.notify.forms.ChannelForm.clean_name", lambda self: "db"),
            mock.patch(
                "apps.console.views.channel.NotificationChannel.objects.create",
                side_effect=IntegrityError("UNIQUE constraint"),
            ),
        ):
            resp = client.post(_url(driver="slack"), _data(name="db", cfg__webhook_url=SLACK_URL))

        assert resp.status_code == 200
        assert "Another channel already has this name." in resp.content.decode()

    @pytest.mark.parametrize("error", [IntegrityError("other"), OperationalError("locked")])
    def test_any_other_failure_is_a_409_that_saves_nothing(self, client, superuser, error):
        with mock.patch(
            "apps.console.views.channel.NotificationChannel.objects.create", side_effect=error
        ):
            resp = client.post(_url(driver="slack"), _data(name="db", cfg__webhook_url=SLACK_URL))

        body = resp.content.decode()
        assert resp.status_code == 409
        assert "Couldn&#x27;t add the channel just now. Nothing was saved, so try again." in body
        assert "Another channel already has this name." not in body
        assert 'name="name" value="db"' in body
        _secrets_absent(body)
        assert not NotificationChannel.objects.exists()

    def test_adding_lands_with_a_message(self, client, superuser):
        resp = client.post(_url(driver="slack"), _data(name="db", cfg__webhook_url=SLACK_URL))

        assert "Channel added." in client.get(resp["Location"]).content.decode()
