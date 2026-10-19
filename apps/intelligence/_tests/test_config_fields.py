"""Tests for the settings each intelligence provider declares."""

import inspect

import pytest
from django.test import override_settings

from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.providers import PROVIDERS
from apps.intelligence.providers.base import BaseProvider
from config.config_fields import ConfigField

KINDS = [key for key, _ in IntelligenceProvider.PROVIDER_CHOICES]
SECRET_WORDS = {"key", "token"}


def init_parameters(cls) -> dict[str, inspect.Parameter]:
    """The keyword parameters of every ``__init__`` in the class's MRO."""
    found: dict[str, inspect.Parameter] = {}
    for klass in reversed(cls.__mro__):
        init = klass.__dict__.get("__init__")
        if init is None:
            continue
        for param in inspect.signature(init).parameters.values():
            if param.name == "self" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            found[param.name] = param
    return found


def settings_problems(cls) -> list[str]:
    params = init_parameters(cls)
    specs = cls.all_config_fields()
    declared = {spec.key for spec in specs}
    problems = [
        f"{name} is neither declared nor unused"
        for name in sorted(set(params) - declared - set(cls.unused_config))
    ]
    problems += [f"{key} is declared but not a parameter" for key in sorted(declared - set(params))]
    for spec in specs:
        if SECRET_WORDS & set(spec.key.split("_")) and not spec.secret:
            problems.append(f"{spec.key} looks like a credential but isn't secret")
        param = params.get(spec.key)
        if param and spec.default is not None and spec.default != param.default:
            problems.append(f"{spec.key} declares a default the provider doesn't use")
    return problems


@pytest.mark.parametrize("kind", KINDS)
def test_every_provider_declares_its_settings(kind):
    assert settings_problems(PROVIDERS[kind]) == []


def test_base_provider_declares_nothing():
    assert BaseProvider.config_fields == ()
    assert BaseProvider.unused_config == ()


class _Base(BaseProvider):
    def __init__(self, api_key: str = "", **kwargs):
        pass

    def analyze(self, incident=None, analysis_type="", path="/"):
        return []


class _Child(_Base):
    def __init__(self, host: str = "http://h", *args, **kwargs):
        super().__init__(**kwargs)


def _fake(fields, unused=()):
    return type("Fake", (_Child,), {"config_fields": tuple(fields), "unused_config": unused})


API_KEY = ConfigField("api_key", "API key", "text", secret=True)
HOST = ConfigField("host", "Host", "url")


def test_guard_collects_parameters_through_the_mro():
    assert set(init_parameters(_Child)) == {"api_key", "host"}


def test_guard_passes_a_fully_declared_provider():
    assert settings_problems(_fake([API_KEY, HOST])) == []


def test_guard_fails_on_an_undeclared_parameter():
    assert settings_problems(_fake([API_KEY])) == ["host is neither declared nor unused"]


def test_guard_accepts_an_unused_parameter():
    assert settings_problems(_fake([HOST], unused=("api_key",))) == []


def test_guard_fails_on_a_declared_key_that_is_no_parameter():
    extra = ConfigField("region", "Region", "text")
    assert settings_problems(_fake([API_KEY, HOST, extra])) == [
        "region is declared but not a parameter"
    ]


def test_guard_fails_on_a_credential_that_is_not_secret():
    plain = ConfigField("api_key", "API key", "text")
    assert settings_problems(_fake([plain, HOST])) == [
        "api_key looks like a credential but isn't secret"
    ]


def test_guard_does_not_read_max_tokens_as_a_credential():
    cls = type(
        "Fake",
        (_Child,),
        {
            "__init__": lambda self, max_tokens=0, **kw: None,
            "config_fields": (API_KEY, HOST, ConfigField("max_tokens", "Most", "int")),
        },
    )
    assert settings_problems(cls) == []


def test_guard_fails_on_a_default_the_provider_does_not_use():
    wrong = ConfigField("host", "Host", "url", default="http://other")
    assert settings_problems(_fake([API_KEY, wrong])) == [
        "host declares a default the provider doesn't use"
    ]


def test_blank_ai_defaults_leave_the_class_defaults():
    cls = PROVIDERS["claude"]
    by_key = {spec.key: spec for spec in cls.all_config_fields()}
    assert [by_key[key].default for key in ("model", "max_tokens", "timeout_s")] == [None] * 3
    assert by_key["api_key"].secret


def test_ollama_takes_a_host_and_no_api_key():
    cls = PROVIDERS["ollama"]
    keys = [spec.key for spec in cls.all_config_fields()]
    assert keys == ["model", "max_tokens", "timeout_s", "host"]
    assert cls.unused_config == ("api_key",)


@pytest.mark.parametrize(
    "kind,default",
    [("copilot", "https://api.githubcopilot.com"), ("grok", "https://api.x.ai/v1")],
)
def test_openai_compatible_providers_take_an_api_address(kind, default):
    by_key = {spec.key: spec for spec in PROVIDERS[kind].all_config_fields()}
    assert by_key["base_url"].kind == "url"
    assert by_key["base_url"].default == default


STORED_SETTINGS = {
    "api_key": "k",
    "model": "stored-model",
    "max_tokens": 111,
    "timeout_s": 7,
    "base_url": "https://proxy.example.com/v1",
    "host": "http://ollama.example.com:11434",
}


@pytest.mark.parametrize("kind", [kind for kind in KINDS if kind in PROVIDERS])
@override_settings(SSRF_ALLOWED_HOSTS=("proxy.example.com", "ollama.example.com"))
def test_every_declared_setting_reaches_the_provider(kind):
    cls = PROVIDERS[kind]
    config = {spec.key: STORED_SETTINGS[spec.key] for spec in cls.all_config_fields()}
    provider = cls(**config)

    assert {key: getattr(provider, key) for key in config} == config
