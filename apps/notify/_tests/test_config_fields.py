"""Tests for the config fields each notify driver declares."""

import inspect
import re

import pytest

from apps.notify import services, templating
from apps.notify.config_fields import COMMON_FIELDS
from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.drivers import base as base_module
from apps.notify.drivers.base import BaseNotifyDriver

ALIASES = {
    "generic": {"webhook_url"},
    "pagerduty": {"dedup_key", "event_action"},
    "*": {"text_template"},
}

_KEY = r"""(["'])(?P<key>[^"']+)\1"""
CONFIG_KEY_PATTERNS = (
    re.compile(r"\bconfig(?:\.get\(\s*|\[\s*)" + _KEY),
    re.compile(r"\.config\s+or\s+\{\}\s*\)\.get\(\s*" + _KEY),
    re.compile(_KEY + r"\s+(?:not\s+)?in\s+config\b"),
)


def _read_keys(source: str) -> set[str]:
    return {m.group("key") for p in CONFIG_KEY_PATTERNS for m in p.finditer(source)}


def test_key_regex_reads_both_quotes_and_whitespace():
    source = """
        config.get("a")
        config.get( 'b', 30)
        config["c"]
        config[ 'd' ]
        other_config.get("e")
        config.get(name)
        if "f" in config
        if 'g' not in config
        "h" in configured
        (channel.config or {}).get("i")
        (channel_obj.config or {}).get( 'j', None)
    """
    assert _read_keys(source) == {"a", "b", "c", "d", "f", "g", "i", "j"}


@pytest.mark.parametrize("name", sorted(DRIVER_REGISTRY))
def test_every_key_a_driver_reads_is_declared(name):
    """Every config key a driver's code reads is a declared field or a listed alias.

    The scan reads literal keys on a variable named ``config`` and on ``(x.config or {})``.
    It can't see non-literal keys, other variable names such as ``payload_config``, or keys
    passed on through ``**config``.

    Aliases are read but kept as unknown keys, never offered in the form:
    - generic ``webhook_url``: an old spelling of ``endpoint``.
    - pagerduty ``dedup_key``: set by ``acknowledge`` and ``resolve``, not an operator setting.
    - pagerduty ``event_action``: set per call, not an operator setting. A channel set to
      resolve would close the incident on every page.
    - ``text_template`` on every driver: the templating service accepts it for ``template``.
    """
    driver = DRIVER_REGISTRY[name]
    source = "\n".join(
        inspect.getsource(module)
        for module in (inspect.getmodule(driver), base_module, templating, services)
    )
    declared = {f.key for f in driver.all_config_fields()}
    allowed = declared | ALIASES.get(name, set()) | ALIASES["*"]

    assert _read_keys(source) - allowed == set()


def test_aliases_are_never_declared():
    for name, cls in DRIVER_REGISTRY.items():
        declared = {f.key for f in cls.all_config_fields()}
        assert declared & (ALIASES.get(name, set()) | ALIASES["*"]) == set()


@pytest.mark.parametrize("name", sorted(DRIVER_REGISTRY))
def test_fields_are_unique_and_worded_for_people(name):
    fields = DRIVER_REGISTRY[name].all_config_fields()
    keys = [f.key for f in fields]

    assert len(keys) == len(set(keys))
    for f in fields:
        for text in (f.label, f.help, *(label for _, label in f.choices)):
            assert "_" not in text and "{" not in text
        assert f.label


@pytest.mark.parametrize("name", sorted(DRIVER_REGISTRY))
def test_a_choice_default_is_one_of_its_choices(name):
    for f in DRIVER_REGISTRY[name].all_config_fields():
        if f.kind == "choice":
            assert f.default in {value for value, _ in f.choices}


def test_common_fields_come_after_the_drivers_own():
    for cls in DRIVER_REGISTRY.values():
        fields = cls.all_config_fields()
        assert fields == cls.config_fields + COMMON_FIELDS


def test_base_driver_declares_only_the_common_fields():
    assert BaseNotifyDriver.config_fields == ()
    assert BaseNotifyDriver.all_config_fields() == COMMON_FIELDS


@pytest.mark.parametrize(
    "name,keys",
    [
        ("slack", ["webhook_url", "timeout"]),
        (
            "email",
            [
                "smtp_host",
                "smtp_port",
                "use_tls",
                "use_ssl",
                "username",
                "password",
                "from_address",
                "to_addresses",
                "timeout",
            ],
        ),
        ("pagerduty", ["integration_key", "client", "client_url", "timeout"]),
        ("generic", ["endpoint", "method", "headers", "timeout", "disabled"]),
    ],
)
def test_driver_fields_in_order(name, keys):
    assert [f.key for f in DRIVER_REGISTRY[name].config_fields] == keys


@pytest.mark.parametrize(
    "name,key,default",
    [
        ("slack", "timeout", 30),
        ("email", "smtp_port", 587),
        ("email", "use_tls", True),
        ("email", "use_ssl", False),
        ("email", "timeout", 30),
        ("pagerduty", "timeout", 30),
        ("generic", "method", "POST"),
        ("generic", "timeout", 30),
        ("generic", "disabled", False),
    ],
)
def test_defaults_match_what_the_driver_falls_back_to(name, key, default):
    fields = {f.key: f for f in DRIVER_REGISTRY[name].config_fields}
    assert fields[key].default == default


def test_credentials_are_secret():
    secret = {
        (name, f.key)
        for name, cls in DRIVER_REGISTRY.items()
        for f in cls.config_fields
        if f.secret
    }
    assert secret == {
        ("slack", "webhook_url"),
        ("email", "password"),
        ("pagerduty", "integration_key"),
        ("generic", "endpoint"),
        ("generic", "headers"),
    }


def test_required_fields_match_validate_config():
    required = {
        (name, f.key)
        for name, cls in DRIVER_REGISTRY.items()
        for f in cls.config_fields
        if f.required
    }
    assert required == {
        ("slack", "webhook_url"),
        ("email", "smtp_host"),
        ("email", "from_address"),
        ("pagerduty", "integration_key"),
    }
