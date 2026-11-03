"""Tests for what each console write records."""

from apps.console import audited


def test_a_provider_key_stays_hidden_with_no_provider_installed(monkeypatch):
    monkeypatch.setattr(audited, "PROVIDER_SECRETS", frozenset())
    monkeypatch.setattr(audited, "PROVIDER_PUBLIC", frozenset())

    changes = audited.provider_changes({"config.api_key": "sk-old"}, {"config.api_key": "sk-new"})

    assert changes == {"config.api_key": {"changed": True}}
