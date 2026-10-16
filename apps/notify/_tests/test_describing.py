import re

import pytest

from apps.notify.describing import FAILURES, describe_delivery, describe_notify
from apps.notify.drivers import DRIVER_REGISTRY
from config.failures import FAILURE_KINDS


def facts(d):
    return [(f.label, f.value) for f in d.facts]


def _delivery(**extra):
    return {
        "driver": "ops-slack",
        "provider": "slack",
        "channel": "#ops",
        "status": "success",
        "provider_id": "",
        "response": {},
        "failure": "",
        **extra,
    }


def _failed(kind, **extra):
    return _delivery(status="failed", failure=kind, **extra)


def test_success_names_channel_and_driver():
    d = describe_delivery(_delivery(provider_id="1700000000.0001"))
    assert d.headline == "Sent to #ops by Slack"
    assert d.tone == "ok"
    assert d.action is None
    assert d.facts[0].label == "Message ID"
    assert d.facts[0].value == "1700000000.0001"
    assert d.facts[0].copy is True


def test_success_without_provider_id_has_no_message_id():
    assert facts(describe_delivery(_delivery())) == []


def test_provider_id_that_is_not_text_is_dropped():
    assert facts(describe_delivery(_delivery(provider_id=["a", "b"]))) == []


def test_numeric_provider_id_is_kept():
    assert facts(describe_delivery(_delivery(provider_id=42))) == [("Message ID", "42")]


def test_every_registered_driver_declares_a_label():
    for key, driver in DRIVER_REGISTRY.items():
        assert driver.label, key


def test_registered_driver_reads_its_label():
    d = describe_delivery(_delivery(provider="pagerduty"))
    assert d.headline == "Sent to #ops by PagerDuty"


def test_registered_driver_without_a_label_is_capitalised(monkeypatch):
    monkeypatch.setattr(DRIVER_REGISTRY["slack"], "label", "")
    assert describe_delivery(_delivery()).headline == "Sent to #ops by Slack"


def test_unknown_provider_key_is_capitalised():
    d = describe_delivery(_delivery(provider="ms_teams"))
    assert d.headline == "Sent to #ops by Ms teams"


@pytest.mark.parametrize("channel", ["", "default", None, 7, "   "])
def test_missing_channel_reads_default(channel):
    d = describe_delivery(_delivery(channel=channel))
    assert d.headline == "Sent to the default channel by Slack"


def test_missing_provider_drops_the_driver():
    assert describe_delivery(_delivery(provider="")).headline == "Sent to #ops"
    d = describe_delivery(_failed("auth", provider=None))
    assert d.headline == "Not sent to #ops: the provider rejected the credentials"


def test_every_failure_kind_is_described():
    assert set(FAILURE_KINDS) <= set(FAILURES)
    for kind in FAILURE_KINDS:
        d = describe_delivery(_failed(kind))
        assert d.headline.startswith("Not sent to #ops")
        assert d.tone == "critical"
        assert d.action is not None


@pytest.mark.parametrize(
    "kind, headline, intent",
    [
        ("auth", "Not sent to #ops: Slack rejected the credentials", "edit_channel"),
        ("rate_limit", "Not sent to #ops: Slack is rate limiting this hub", ""),
        ("timeout", "Not sent to #ops: Slack didn't answer in time", ""),
        ("provider_down", "Not sent to #ops: Slack is unreachable", ""),
        ("rejected", "Not sent to #ops: Slack refused the message", "edit_channel"),
        ("unknown", "Not sent to #ops", "edit_channel"),
        ("", "Not sent to #ops", "edit_channel"),
        ("gremlins", "Not sent to #ops", "edit_channel"),
        (None, "Not sent to #ops", "edit_channel"),
    ],
)
def test_failure_kinds(kind, headline, intent):
    d = describe_delivery(_failed(kind))
    assert d.headline == headline
    assert d.action.intent == intent


def test_edit_channel_names_the_channel_record():
    d = describe_delivery(_failed("auth"))
    assert d.action.params == {"name": "ops-slack"}


def test_edit_channel_without_a_record_name_has_no_params():
    d = describe_delivery(_failed("auth", driver=""))
    assert d.action.intent == "edit_channel"
    assert d.action.params == {}


def test_text_only_action_has_no_params():
    assert describe_delivery(_failed("timeout")).action.params == {}


def test_error_text_goes_into_facts():
    d = describe_delivery(
        _failed("auth", response={"success": False, "error": "invalid_auth", "status_code": 401})
    )
    assert "invalid_auth" not in d.headline
    assert facts(d) == [("HTTP status", "401"), ("Error", "invalid_auth")]


def test_message_is_the_error_when_error_is_missing():
    d = describe_delivery(_failed("rejected", response={"message": "channel_not_found"}))
    assert facts(d) == [("Error", "channel_not_found")]


@pytest.mark.parametrize(
    "response",
    [None, "boom", ["x"], {"error": {"nested": 1}, "status_code": True}, {"status_code": "500"}],
)
def test_odd_responses_never_raise(response):
    d = describe_delivery(_failed("unknown", response=response))
    assert d.headline == "Not sent to #ops"
    assert facts(d) == []


def test_status_other_than_success_is_a_failure():
    d = describe_delivery({"status": "weird", "channel": "#ops", "provider": "email"})
    assert d.headline == "Not sent to #ops"
    assert d.tone == "critical"


@pytest.mark.parametrize("delivery", [None, [], "slack", 3])
def test_non_dict_delivery_never_raises(delivery):
    d = describe_delivery(delivery)
    assert d.headline == "Not sent to the default channel"


def test_describe_notify_one_per_delivery():
    snapshot = {
        "deliveries": [_delivery(), "junk", None, _failed("timeout", channel="#db")],
        "channels_attempted": 2,
    }
    headlines = [d.headline for d in describe_notify(snapshot)]
    assert headlines == ["Sent to #ops by Slack", "Not sent to #db: Slack didn't answer in time"]


@pytest.mark.parametrize(
    "snapshot",
    [None, {}, [], "x", {"deliveries": None}, {"deliveries": "x"}, {"deliveries": [1, None]}],
)
def test_describe_notify_nothing_sent(snapshot):
    (d,) = describe_notify(snapshot)
    assert d.headline == "Nothing was sent"
    assert d.tone == "muted"


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")


@pytest.mark.parametrize(
    "delivery",
    [
        *(_failed(kind, response={"error": "invalid_auth"}) for kind in FAILURE_KINDS),
        _failed("gremlins"),
        _delivery(provider_id="abc"),
        _delivery(channel="", provider="pagerduty"),
        None,
    ],
)
def test_wording_is_plain(delivery):
    d = describe_delivery(delivery)
    texts = [d.headline, d.detail] + ([d.action.text] if d.action else [])
    for text in texts:
        assert not _UUID.search(text) and not _SNAKE.search(text) and "{" not in text


def test_nothing_sent_wording_is_plain():
    (d,) = describe_notify(None)
    assert not _SNAKE.search(d.headline) and "{" not in d.headline
