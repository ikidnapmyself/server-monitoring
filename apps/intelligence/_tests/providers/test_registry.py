"""Tests for the intelligence provider registry and get_active_provider."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.providers import (
    PROVIDERS,
    get_active_provider,
    list_providers,
)
from apps.intelligence.providers.local import LocalRecommendationProvider


class TestProviderRegistry(SimpleTestCase):
    """Tests for PROVIDERS registry."""

    def test_all_expected_providers_registered(self):
        expected = {"local", "openai", "claude", "gemini", "copilot", "grok", "ollama", "mistral"}
        assert expected.issubset(set(PROVIDERS.keys()))

    def test_list_providers_returns_all(self):
        providers = list_providers()
        assert "local" in providers
        assert "openai" in providers
        assert "claude" in providers
        assert "gemini" in providers
        assert "copilot" in providers
        assert "grok" in providers
        assert "ollama" in providers
        assert "mistral" in providers


class TestGetActiveProvider(TestCase):
    """Tests for get_active_provider (requires DB)."""

    def test_no_active_provider_returns_local(self):
        provider = get_active_provider()
        assert isinstance(provider, LocalRecommendationProvider)

    def test_active_provider_returns_configured_instance(self):
        IntelligenceProvider.objects.create(
            name="test-claude",
            provider="claude",
            config={"api_key": "sk-test-123", "model": "claude-sonnet-4-20250514"},
            is_active=True,
        )
        provider = get_active_provider()
        assert provider.__class__.__name__ == "ClaudeRecommendationProvider"
        assert provider.api_key == "sk-test-123"
        assert provider.model == "claude-sonnet-4-20250514"

    def test_active_provider_with_unknown_driver_falls_back_to_local(self):
        IntelligenceProvider.objects.create(
            name="test-unknown",
            provider="openai",
            config={"api_key": "key"},
            is_active=True,
        )
        # Use patch.dict to temporarily remove openai without mutating dict ordering
        providers_without_openai = {k: v for k, v in PROVIDERS.items() if k != "openai"}
        with patch.dict(
            "apps.intelligence.providers.PROVIDERS", providers_without_openai, clear=True
        ):
            provider = get_active_provider()
            assert isinstance(provider, LocalRecommendationProvider)

    def test_inactive_provider_ignored(self):
        IntelligenceProvider.objects.create(
            name="test-openai",
            provider="openai",
            config={"api_key": "key"},
            is_active=False,
        )
        provider = get_active_provider()
        assert isinstance(provider, LocalRecommendationProvider)

    def test_db_error_falls_back_to_local(self):
        with patch("apps.intelligence.models.IntelligenceProvider.objects") as mock_objects:
            mock_objects.filter.side_effect = Exception("DB error")
            provider = get_active_provider()
            assert isinstance(provider, LocalRecommendationProvider)

    def test_kwargs_passed_to_provider(self):
        IntelligenceProvider.objects.create(
            name="test-openai",
            provider="openai",
            config={"api_key": "db-key"},
            is_active=True,
        )
        provider = get_active_provider(model="gpt-4o")
        assert provider.model == "gpt-4o"
