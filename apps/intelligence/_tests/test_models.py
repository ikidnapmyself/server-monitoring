"""Tests for intelligence models."""

import pytest

from apps.intelligence.models import IntelligenceProvider


@pytest.mark.django_db
class TestIntelligenceProvider:
    """Tests for IntelligenceProvider model."""

    def test_str_active(self):
        provider = IntelligenceProvider.objects.create(
            name="test-claude",
            provider="claude",
            is_active=True,
        )
        assert str(provider) == "test-claude (claude) [active]"

    def test_str_inactive(self):
        provider = IntelligenceProvider.objects.create(
            name="test-openai",
            provider="openai",
            is_active=False,
        )
        assert str(provider) == "test-openai (openai) [inactive]"

    def test_ordering(self):
        IntelligenceProvider.objects.create(name="z-provider", provider="openai")
        IntelligenceProvider.objects.create(name="a-provider", provider="claude")
        IntelligenceProvider.objects.create(name="m-provider", provider="gemini")

        names = list(IntelligenceProvider.objects.values_list("name", flat=True))
        assert names == ["a-provider", "m-provider", "z-provider"]

    def test_save_deactivates_others(self):
        p1 = IntelligenceProvider.objects.create(name="first", provider="openai", is_active=True)
        p2 = IntelligenceProvider.objects.create(name="second", provider="claude", is_active=True)

        p1.refresh_from_db()
        assert p1.is_active is False
        assert p2.is_active is True

    def test_save_inactive_does_not_deactivate_others(self):
        p1 = IntelligenceProvider.objects.create(name="first", provider="openai", is_active=True)
        IntelligenceProvider.objects.create(name="second", provider="claude", is_active=False)

        p1.refresh_from_db()
        assert p1.is_active is True

    def test_config_defaults_to_empty_dict(self):
        provider = IntelligenceProvider.objects.create(name="test", provider="openai")
        assert provider.config == {}

    def test_config_stores_json(self):
        provider = IntelligenceProvider.objects.create(
            name="test",
            provider="claude",
            config={"api_key": "sk-test", "model": "claude-sonnet-4-20250514"},
        )
        provider.refresh_from_db()
        assert provider.config["api_key"] == "sk-test"
        assert provider.config["model"] == "claude-sonnet-4-20250514"

    def test_description_defaults_to_empty(self):
        provider = IntelligenceProvider.objects.create(name="test", provider="openai")
        assert provider.description == ""

    def test_timestamps_auto_set(self):
        provider = IntelligenceProvider.objects.create(name="test", provider="openai")
        assert provider.created_at is not None
        assert provider.updated_at is not None

    def test_name_unique_constraint(self):
        IntelligenceProvider.objects.create(name="unique", provider="openai")
        with pytest.raises(Exception):
            IntelligenceProvider.objects.create(name="unique", provider="claude")

    def test_reactivate_same_provider(self):
        """Saving an already-active provider again should keep it active."""
        p1 = IntelligenceProvider.objects.create(name="only", provider="openai", is_active=True)
        p1.save()
        p1.refresh_from_db()
        assert p1.is_active is True
