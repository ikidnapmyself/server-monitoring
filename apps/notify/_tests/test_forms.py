"""The typed channel form: what an operator edits, and what it writes back."""

import re

import pytest
from django import forms

from apps.notify.config_fields import ConfigField
from apps.notify.drivers import DRIVER_REGISTRY, GenericNotifyDriver
from apps.notify.forms import ALIAS_HELP, UNKNOWN_DRIVER, ChannelForm
from apps.notify.models import NotificationChannel

pytestmark = pytest.mark.django_db

SLACK_URL = "https://hooks.slack.com/services/T000/B000/abc123secret"
PD_KEY = "pdkey-0123456789abcdef0123"

STORED = {
    "slack": {"webhook_url": SLACK_URL, "timeout": 10, "template": "slack_text.j2"},
    "email": {
        "smtp_host": "mail.example.com",
        "smtp_port": "2525",
        "use_tls": False,
        "username": "ops",
        "password": "hunter2-password",
        "from_address": "hub@example.com",
        "to_addresses": ["a@example.com", "b@example.com"],
        "text_template": "legacy.j2",
    },
    "pagerduty": {
        "integration_key": PD_KEY,
        "client": "hub",
        "client_url": "https://hub.example.com/",
        "dedup_key": "dedup-value-xyz",
        "event_action": "trigger",
    },
    "generic": {
        "endpoint": "https://example.com/hook-endpoint-secret",
        "method": "get",
        "headers": {"Authorization": "Bearer header-secret-value"},
        "disabled": False,
        "payload_template": '{"text": "{{ title }}"}',
        "webhook_url": "https://example.com/alias-secret-value",
    },
}

SECRETS = ("webhook_url", "password", "integration_key", "endpoint", "headers")


def make(driver, config, name="ops", **kwargs):
    return NotificationChannel.objects.create(name=name, driver=driver, config=config, **kwargs)


def initial_data(form):
    """What a browser posts back for a form opened and saved untouched."""
    data = {}
    for bound in form:
        value = bound.value()
        if isinstance(bound.field.widget, forms.CheckboxInput):
            if value:
                data[bound.html_name] = "on"
        elif value is not None:
            data[bound.html_name] = re.sub(r"\r?\n", "\r\n", str(value))
    return data


def resubmit(channel, **changes):
    data = initial_data(ChannelForm(channel=channel))
    data.update(changes)
    return ChannelForm(data, channel=channel)


@pytest.mark.parametrize("driver", sorted(STORED))
def test_an_untouched_save_writes_back_exactly_what_was_stored(driver):
    channel = make(driver, STORED[driver])
    form = resubmit(channel)

    assert form.is_valid(), form.errors
    assert form.config() == STORED[driver]
    assert form.channel_fields() == {
        "name": "ops",
        "driver": driver,
        "is_active": True,
        "description": "",
        "config": STORED[driver],
    }


def test_an_empty_generic_config_stays_empty():
    form = resubmit(make("generic", {}))

    assert form.is_valid(), form.errors
    assert form.config() == {}


@pytest.mark.parametrize("driver", ["slack", "email", "pagerduty"])
def test_an_empty_config_for_a_driver_with_required_fields_is_not_saved(driver):
    form = resubmit(make(driver, {}))

    assert not form.is_valid()
    assert form.errors


def test_defaults_are_shown_but_not_written():
    form = ChannelForm(channel=make("generic", {}))

    assert form["cfg__method"].value() == "POST"
    assert form["cfg__timeout"].value() == "30"
    assert form["cfg__disabled"].value() is False


def test_a_stored_choice_outside_the_list_is_offered_as_stored():
    form = ChannelForm(channel=make("generic", STORED["generic"]))

    assert ("get", "get (stored)") in form.fields["cfg__method"].choices


def test_an_unchecked_box_writes_false_only_when_it_differs_or_was_stored():
    channel = make("email", {"smtp_host": "h", "from_address": "f", "use_ssl": True})
    data = initial_data(ChannelForm(channel=channel))
    del data["cfg__use_tls"]
    del data["cfg__use_ssl"]
    form = ChannelForm(data, channel=channel)

    assert form.is_valid(), form.errors
    assert form.config() == {
        "smtp_host": "h",
        "from_address": "f",
        "use_tls": False,
        "use_ssl": False,
    }


def test_a_changed_value_writes_its_cleaned_form():
    form = resubmit(make("email", STORED["email"]), cfg__smtp_port="465")

    assert form.is_valid(), form.errors
    assert form.config()["smtp_port"] == 465


def test_a_new_value_for_a_key_not_stored_is_written():
    form = resubmit(make("generic", {}), cfg__timeout="5", cfg__method="PUT", cfg__disabled="on")

    assert form.is_valid(), form.errors
    assert form.config() == {"timeout": 5, "method": "PUT", "disabled": True}


def test_an_optional_int_left_blank_drops_its_key():
    form = resubmit(make("slack", STORED["slack"]), cfg__timeout="")

    assert form.is_valid(), form.errors
    assert "timeout" not in form.config()


def test_an_int_below_one_is_refused():
    form = resubmit(make("slack", STORED["slack"]), cfg__timeout="0")

    assert not form.is_valid()
    assert "cfg__timeout" in form.errors


def test_an_invalid_stored_value_left_untouched_is_kept():
    stored = dict(STORED["pagerduty"], client_url="not a url", timeout=0)
    form = resubmit(make("pagerduty", stored))

    assert form.is_valid(), form.errors
    assert form.config() == stored


def test_a_changed_invalid_url_is_refused():
    form = resubmit(make("pagerduty", STORED["pagerduty"]), cfg__client_url="ftp://x.example")

    assert not form.is_valid()
    assert "cfg__client_url" in form.errors


def test_a_blank_optional_choice_drops_its_key():
    form = resubmit(make("generic", STORED["generic"]), cfg__method="")

    assert form.is_valid(), form.errors
    assert "method" not in form.config()


def test_a_list_is_one_value_per_line():
    form = resubmit(
        make("email", STORED["email"]), cfg__to_addresses=" x@example.com \r\n\r\ny@example.com"
    )

    assert form.is_valid(), form.errors
    assert form.config()["to_addresses"] == ["x@example.com", "y@example.com"]


def test_an_emptied_list_drops_its_key():
    form = resubmit(make("email", STORED["email"]), cfg__to_addresses="")

    assert form.is_valid(), form.errors
    assert "to_addresses" not in form.config()


def test_a_stored_list_that_is_a_string_round_trips():
    stored = {"smtp_host": "h", "from_address": "f", "to_addresses": "a@example.com"}
    form = resubmit(make("email", stored))

    assert form.is_valid(), form.errors
    assert form.config() == stored


def test_headers_are_one_name_and_value_per_line():
    form = resubmit(
        make("generic", STORED["generic"]),
        cfg__headers="X-One: 1\r\n\r\nX-Two:  two: parts ",
    )

    assert form.is_valid(), form.errors
    assert form.config()["headers"] == {"X-One": "1", "X-Two": "two: parts"}


@pytest.mark.parametrize("bad", ["no colon here", ": no name"])
def test_a_header_line_without_a_name_and_value_is_refused(bad):
    form = resubmit(make("generic", STORED["generic"]), cfg__headers=bad)

    assert not form.is_valid()
    assert form.errors["cfg__headers"] == ["Write each header on its own line as Name: value."]


def test_a_template_keeps_its_whitespace_and_unix_newlines():
    form = resubmit(make("slack", STORED["slack"]), cfg__template="  line one\r\nline two\r\n")

    assert form.is_valid(), form.errors
    assert form.config()["template"] == "  line one\nline two\n"


def test_a_blank_secret_keeps_the_stored_one():
    form = resubmit(make("slack", STORED["slack"]))

    assert form["cfg__webhook_url"].value() is None
    assert form.is_valid(), form.errors
    assert form.config()["webhook_url"] == SLACK_URL


def test_a_new_secret_replaces_the_stored_one():
    new = "https://hooks.slack.com/services/T000/B000/new"
    form = resubmit(make("slack", STORED["slack"]), cfg__webhook_url=new)

    assert form.is_valid(), form.errors
    assert form.config()["webhook_url"] == new


def test_a_cleared_secret_is_dropped():
    form = resubmit(make("email", STORED["email"]), cfg__password__clear="on")

    assert form.is_valid(), form.errors
    assert "password" not in form.config()


def test_a_cleared_required_secret_is_an_error():
    form = resubmit(make("slack", STORED["slack"]), cfg__webhook_url__clear="on")

    assert not form.is_valid()
    assert form.errors["cfg__webhook_url"] == ["Webhook URL is required."]


def test_a_required_secret_with_nothing_stored_is_an_error():
    form = resubmit(make("slack", {}))

    assert not form.is_valid()
    assert form.errors["cfg__webhook_url"] == ["Webhook URL is required."]


def test_a_required_field_left_blank_is_an_error():
    form = resubmit(make("email", STORED["email"]), cfg__smtp_host="")

    assert not form.is_valid()
    assert form.errors["cfg__smtp_host"] == ["SMTP host is required."]


def test_a_secret_says_whether_it_is_set_and_offers_clear_only_then():
    form = ChannelForm(channel=make("email", {"smtp_host": "h", "from_address": "f"}))
    assert form.fields["cfg__password"].help_text == "Not set."
    assert "cfg__password__clear" not in form.fields

    form = ChannelForm(channel=make("slack", STORED["slack"], name="other"))
    assert form.fields["cfg__webhook_url"].help_text == (
        "The incoming webhook address Slack gave you. Set. Leave blank to keep it."
    )
    assert form.fields["cfg__webhook_url__clear"].label == "Clear"


@pytest.mark.parametrize("driver", sorted(STORED))
def test_no_secret_or_unknown_value_is_ever_rendered(driver):
    channel = make(driver, STORED[driver])
    unshown = [
        str(value)
        for key, value in STORED[driver].items()
        if key in SECRETS or key in ChannelForm(channel=channel).unknown_keys()
    ]
    unshown += ["header-secret-value"] if driver == "generic" else []

    pages = [str(ChannelForm(channel=channel))]
    typed = resubmit(channel, cfg__timeout="0", **{f"cfg__{k}": "typed-secret" for k in SECRETS})
    assert not typed.is_valid()
    pages.append(str(typed))

    for page in pages:
        for value in unshown:
            assert value not in page
        assert "typed-secret" not in page


def test_unknown_keys_are_listed_by_name():
    form = ChannelForm(channel=make("generic", STORED["generic"]))

    assert form.unknown_keys() == ["webhook_url"]


def test_switching_slack_to_email_drops_the_webhook():
    channel = make("slack", STORED["slack"])
    data = initial_data(ChannelForm(channel=channel, driver="email"))
    data.update(cfg__smtp_host="h", cfg__from_address="f")
    form = ChannelForm(data, channel=channel, driver="email")

    assert form.is_valid(), form.errors
    assert form.config() == {
        "smtp_host": "h",
        "from_address": "f",
        "timeout": 10,
        "template": "slack_text.j2",
    }


def test_switching_away_from_generic_drops_its_old_webhook_spelling():
    channel = make("generic", STORED["generic"])
    form = ChannelForm(channel=channel, driver="slack")

    assert form.fields["cfg__webhook_url"].help_text.endswith("Not set.")
    assert "webhook_url" not in form.unknown_keys()
    assert "endpoint" not in form.unknown_keys()
    assert "headers" not in form.unknown_keys()


def test_the_settings_shown_must_match_the_driver_submitted():
    channel = make("slack", STORED["slack"])
    data = initial_data(ChannelForm(channel=channel))
    data["driver"] = "email"
    form = ChannelForm(data, channel=channel)

    assert not form.is_valid()
    assert form.errors["driver"] == ["Show this driver's settings before saving."]


def test_an_unknown_driver_keeps_everything_and_renders_no_typed_field():
    stored = {"coop": "north", "bird_count": 3}
    channel = make("carrier_pigeon", stored)
    form = ChannelForm(channel=channel)

    assert ("carrier_pigeon", UNKNOWN_DRIVER) in form.fields["driver"].choices
    assert form.driver_label == UNKNOWN_DRIVER
    assert not [name for name in form.fields if name.startswith("cfg__")]
    assert form.unknown_keys() == ["bird_count", "coop"]
    assert form.groups() == [("Settings", []), ("Message templates", [])]

    form = resubmit(channel)
    assert form.is_valid(), form.errors
    assert form.config() == stored


def test_validate_config_false_is_a_non_field_error():
    form = resubmit(
        make("slack", STORED["slack"]), cfg__webhook_url="https://example.com/not-slack"
    )

    assert not form.is_valid()
    assert form.non_field_errors() == [
        "Slack can't send with these settings. Check the required fields."
    ]


def test_disabled_disables_every_field():
    form = ChannelForm(channel=make("email", STORED["email"]), disabled=True)

    assert form.fields
    assert all(field.disabled for field in form.fields.values())


def test_a_name_another_channel_has_is_an_error():
    make("generic", {}, name="taken")
    form = resubmit(make("generic", {}), name="taken")

    assert not form.is_valid()
    assert form.errors["name"] == ["Another channel already has this name."]


def test_a_new_channel_starts_on_the_driver_asked_for():
    form = ChannelForm(driver="generic")

    assert form["driver"].value() == "generic"
    assert form["is_active"].value() is True
    assert form.unknown_keys() == []

    data = initial_data(form)
    data["name"] = "fresh"
    form = ChannelForm(data, driver="generic")
    assert form.is_valid(), form.errors
    assert form.channel_fields()["config"] == {}


def test_every_registered_driver_is_offered_by_its_label():
    form = ChannelForm(driver="slack")

    assert form.fields["driver"].choices == [
        (key, cls.label) for key, cls in DRIVER_REGISTRY.items()
    ]
    assert form.driver_label == "Slack"


def test_groups_put_templates_apart_and_each_clear_after_its_secret():
    form = ChannelForm(channel=make("slack", STORED["slack"]))
    groups = dict(form.groups())

    assert [b.name for b in groups["Settings"]] == [
        "cfg__webhook_url",
        "cfg__webhook_url__clear",
        "cfg__timeout",
        "cfg__channel",
    ]
    assert [b.name for b in groups["Message templates"]] == [
        "cfg__template",
        "cfg__html_template",
        "cfg__payload_template",
    ]


def test_a_stored_config_that_is_not_a_dict_reads_as_empty():
    form = ChannelForm(channel=make("generic", ["junk"]))

    assert form.unknown_keys() == []


def test_a_choice_with_no_default_offers_not_set(monkeypatch):
    region = ConfigField("region", "Region", "choice", choices=(("eu", "Europe"),))
    monkeypatch.setattr(GenericNotifyDriver, "config_fields", (region,))
    channel = make("generic", {})

    assert ChannelForm(channel=channel).fields["cfg__region"].choices[0] == ("", "Not set")
    form = resubmit(channel)
    assert form.is_valid(), form.errors
    assert form.config() == {}


def test_a_stored_crlf_survives_an_untouched_save():
    stored = dict(STORED["slack"], template="a\r\nb")
    form = resubmit(make("slack", stored))

    assert form.is_valid(), form.errors
    assert form.config() == stored


def test_switching_away_from_an_unknown_driver_starts_empty():
    channel = make("teams", {"webhook": "https://teams.example.com/teams-secret", "n": "x9z"})
    form = ChannelForm(channel=channel, driver="slack")
    page = str(form)

    assert form.unknown_keys() == []
    assert "teams-secret" not in page
    assert "x9z" not in page


def test_an_old_spelling_of_a_secret_reads_as_set_and_clear_drops_it():
    stored = {"webhook_url": "https://example.com/old-name-secret", "timeout": 5}
    channel = make("generic", stored)
    form = ChannelForm(channel=channel)

    assert form.fields["cfg__endpoint"].help_text == ALIAS_HELP
    assert "old-name-secret" not in str(form)

    kept = resubmit(channel)
    assert kept.is_valid(), kept.errors
    assert kept.config() == stored

    cleared = resubmit(channel, cfg__endpoint__clear="on", cfg__disabled="on")
    assert cleared.is_valid(), cleared.errors
    assert cleared.config() == {"timeout": 5, "disabled": True}
