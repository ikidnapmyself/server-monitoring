"""The typed provider form: what an operator edits, and what it writes back."""

import re
import socket
from typing import Any

import pytest
from django import forms
from django.test import override_settings

from apps.intelligence.forms import UNKNOWN_PROVIDER, UNSAFE_URL, ProviderForm
from apps.intelligence.models import IntelligenceProvider

pytestmark = pytest.mark.django_db

KEY = "sk-stored-secret-0123456789"

STORED: dict[str, dict[str, Any]] = {
    "openai": {"api_key": KEY, "model": "gpt-4o", "max_tokens": 2048, "timeout_s": 45},
    "claude": {"api_key": KEY, "model": "claude-sonnet-4-20250514"},
    "gemini": {"api_key": KEY, "max_tokens": "512"},
    "copilot": {"api_key": KEY, "base_url": "https://api.githubcopilot.com"},
    "grok": {"api_key": KEY, "base_url": "https://proxy.example.com/v1", "timeout_s": 60},
    "ollama": {"host": "http://ollama.example.com:11434", "model": "llama3.1"},
    "mistral": {"api_key": KEY, "timeout_s": 20, "temperature": 0.1},
}

PRIVATE = {"localhost": "127.0.0.1", "internal.example.com": "10.0.0.5"}


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    def resolve(host, port, *args, **kwargs):
        address = PRIVATE.get(host, "93.184.216.34")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def make(kind, config, name="ai", **kwargs):
    return IntelligenceProvider.objects.create(name=name, provider=kind, config=config, **kwargs)


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


def resubmit(instance, kind=None, **changes):
    data = initial_data(ProviderForm(provider=instance, kind=kind))
    data.update(changes)
    return ProviderForm(data, provider=instance, kind=kind)


def test_every_provider_kind_has_a_stored_example():
    assert sorted(STORED) == sorted(k for k, _ in IntelligenceProvider.PROVIDER_CHOICES)


@pytest.mark.parametrize("kind", sorted(STORED))
def test_an_untouched_save_writes_back_exactly_what_was_stored(kind):
    form = resubmit(make(kind, STORED[kind]))

    assert form.is_valid(), form.errors
    assert form.config() == STORED[kind]


@pytest.mark.parametrize("kind", sorted(STORED))
def test_an_untouched_new_provider_stores_nothing(kind):
    data = initial_data(ProviderForm(kind=kind))
    data["name"] = "fresh"
    with override_settings(SSRF_ALLOWED_HOSTS=("localhost",)):
        form = ProviderForm(data, kind=kind)
        assert form.is_valid(), form.errors
    assert form.provider_fields() == {
        "name": "fresh",
        "provider": kind,
        "is_active": False,
        "description": "",
        "config": {},
    }


def test_a_blank_secret_keeps_the_stored_one():
    form = resubmit(make("claude", STORED["claude"]), cfg__api_key="")

    assert form.is_valid(), form.errors
    assert form.config()["api_key"] == KEY


def test_a_new_secret_replaces_the_stored_one():
    form = resubmit(make("claude", STORED["claude"]), cfg__api_key="sk-new")

    assert form.is_valid(), form.errors
    assert form.config()["api_key"] == "sk-new"


def test_clear_removes_the_stored_secret():
    form = resubmit(make("claude", STORED["claude"]), cfg__api_key__clear="on")

    assert form.is_valid(), form.errors
    assert "api_key" not in form.config()


def test_unknown_keys_are_kept_and_listed():
    provider = make("mistral", STORED["mistral"])

    assert ProviderForm(provider=provider).unknown_keys() == ["temperature"]
    form = resubmit(provider, cfg__timeout_s="25")
    assert form.is_valid(), form.errors
    assert form.config() == dict(STORED["mistral"], timeout_s=25)


def test_switching_kind_drops_the_api_key():
    provider = make("claude", STORED["claude"])
    form = resubmit(provider, kind="openai")

    assert form.fields["cfg__api_key"].help_text == "Not set."
    assert form.is_valid(), form.errors
    assert form.config() == {"model": "claude-sonnet-4-20250514"}


def test_switching_to_ollama_drops_the_api_key_it_does_not_take():
    provider = make("openai", STORED["openai"])
    form = resubmit(provider, kind="ollama", cfg__host="")

    assert form.is_valid(), form.errors
    assert form.config() == {"model": "gpt-4o", "max_tokens": 2048, "timeout_s": 45}


def test_the_settings_shown_must_match_the_kind_submitted():
    form = resubmit(make("claude", STORED["claude"]), provider="openai")

    assert not form.is_valid()
    assert form.errors["provider"] == ["Show this provider's settings before saving."]


def test_an_unknown_kind_keeps_everything_and_renders_no_typed_field():
    stored = {"region": "north", "api_key": KEY}
    provider = make("watson", stored)
    form = ProviderForm(provider=provider)

    assert ("watson", UNKNOWN_PROVIDER) in form.fields["provider"].choices
    assert form.provider_label == UNKNOWN_PROVIDER
    assert not [name for name in form.fields if name.startswith("cfg__")]
    assert form.unknown_keys() == ["api_key", "region"]
    assert form.groups() == [("Settings", [])]

    form = resubmit(provider)
    assert form.is_valid(), form.errors
    assert form.config() == stored


def test_switching_away_from_an_unknown_kind_starts_empty():
    provider = make("watson", {"region": "x9z-region"})
    form = ProviderForm(provider=provider, kind="claude")

    assert form.unknown_keys() == []
    assert "x9z-region" not in str(form)


def test_an_unsafe_host_is_a_field_error():
    form = resubmit(make("ollama", STORED["ollama"]), cfg__host="http://localhost:11434")

    assert not form.is_valid()
    assert form.errors["cfg__host"] == [UNSAFE_URL]


@override_settings(SSRF_ALLOWED_HOSTS=("localhost",))
def test_a_host_the_hub_allows_passes():
    form = resubmit(make("ollama", STORED["ollama"]), cfg__host="http://localhost:11434")

    assert form.is_valid(), form.errors
    assert form.config()["host"] == "http://localhost:11434"


def test_an_unsafe_api_address_is_a_field_error():
    form = resubmit(make("grok", STORED["grok"]), cfg__base_url="http://internal.example.com/v1")

    assert not form.is_valid()
    assert form.errors["cfg__base_url"] == [UNSAFE_URL]


def test_a_blanked_address_drops_its_key():
    form = resubmit(make("grok", STORED["grok"]), cfg__base_url="")

    assert form.is_valid(), form.errors
    assert "base_url" not in form.config()


@pytest.mark.parametrize("kind", sorted(k for k in STORED if "api_key" in STORED[k]))
def test_the_stored_api_key_is_never_rendered(kind):
    provider = make(kind, STORED[kind])
    invalid = resubmit(provider, cfg__max_tokens="0", cfg__api_key="typed-secret")

    assert not invalid.is_valid()
    for page in (str(ProviderForm(provider=provider)), str(resubmit(provider)), str(invalid)):
        assert KEY not in page
        assert "typed-secret" not in page


def test_a_name_another_provider_has_is_an_error():
    make("claude", {}, name="taken")
    form = resubmit(make("claude", {}), name="taken")

    assert not form.is_valid()
    assert form.errors["name"] == ["Another provider already has this name."]


def test_every_choice_is_offered_by_its_label():
    form = ProviderForm(kind="grok")

    assert form.fields["provider"].choices == IntelligenceProvider.PROVIDER_CHOICES
    assert form.provider_label == "Grok (xAI)"
    assert form["is_active"].value() is False


def test_disabled_disables_every_field():
    form = ProviderForm(provider=make("claude", STORED["claude"]), disabled=True)

    assert form.fields
    assert all(field.disabled for field in form.fields.values())


def test_groups_put_each_clear_after_its_secret():
    form = ProviderForm(provider=make("claude", STORED["claude"]))

    assert [[b.name for b in group] for _, group in form.groups()] == [
        [
            "cfg__api_key",
            "cfg__api_key__clear",
            "cfg__model",
            "cfg__max_tokens",
            "cfg__timeout_s",
        ]
    ]
    assert form.groups()[0][0] == "Settings"


def test_a_stored_config_that_is_not_a_dict_reads_as_empty():
    assert ProviderForm(provider=make("claude", ["junk"])).unknown_keys() == []
