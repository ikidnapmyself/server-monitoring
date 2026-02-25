"""Tests for the ClaudeRecommendationProvider."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.intelligence.providers import RecommendationType
from apps.intelligence.providers.claude import ClaudeRecommendationProvider


class TestClaudeProviderInitialization(SimpleTestCase):
    """Tests for Claude provider initialization."""

    def test_initialization_defaults(self):
        provider = ClaudeRecommendationProvider(api_key="test-key")
        assert provider.api_key == "test-key"
        assert provider.model == "claude-sonnet-4-20250514"
        assert provider.max_tokens == 1024

    def test_initialization_custom_values(self):
        provider = ClaudeRecommendationProvider(
            api_key="custom-key", model="claude-opus-4-20250514", max_tokens=2048
        )
        assert provider.api_key == "custom-key"
        assert provider.model == "claude-opus-4-20250514"
        assert provider.max_tokens == 2048

    def test_provider_attributes(self):
        provider = ClaudeRecommendationProvider(api_key="test-key")
        assert provider.name == "claude"
        assert provider.description == "Claude (Anthropic) intelligence provider"


class TestClaudeCallApi(SimpleTestCase):
    """Tests for Claude API calls."""

    @patch("anthropic.Anthropic")
    def test_call_api_success(self, mock_anthropic_class):
        mock_message = MagicMock()
        mock_message.content = [MagicMock()]
        mock_message.content[0].text = '{"test": "response"}'

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_anthropic_class.return_value = mock_client

        provider = ClaudeRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == '{"test": "response"}'
        mock_anthropic_class.assert_called_once_with(api_key="test-key")
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["system"] == ClaudeRecommendationProvider.SYSTEM_PROMPT
        assert call_kwargs["messages"] == [{"role": "user", "content": "Test prompt"}]

    @patch("anthropic.Anthropic")
    def test_call_api_custom_model(self, mock_anthropic_class):
        mock_message = MagicMock()
        mock_message.content = [MagicMock()]
        mock_message.content[0].text = "response"

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_anthropic_class.return_value = mock_client

        provider = ClaudeRecommendationProvider(
            api_key="key", model="claude-opus-4-20250514", max_tokens=4096
        )
        provider._call_api("prompt")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-20250514"
        assert call_kwargs["max_tokens"] == 4096


class TestClaudeAnalyze(SimpleTestCase):
    """Tests for end-to-end analysis."""

    @patch.object(ClaudeRecommendationProvider, "_call_api")
    def test_analyze_success(self, mock_call_api):
        mock_call_api.return_value = json.dumps(
            [
                {
                    "type": "memory",
                    "priority": "high",
                    "title": "Memory Issue",
                    "description": "High memory usage detected",
                    "actions": ["Restart service"],
                }
            ]
        )

        provider = ClaudeRecommendationProvider(api_key="test-key")
        incident = MagicMock()
        incident.id = 1
        incident.title = "Test"
        incident.description = "Test"
        del incident.severity
        del incident.status
        del incident.alerts
        del incident.metadata

        recommendations = provider.analyze(incident)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.MEMORY
        assert recommendations[0].incident_id == 1

    @patch.object(ClaudeRecommendationProvider, "_call_api")
    def test_analyze_api_error_returns_fallback(self, mock_call_api):
        mock_call_api.side_effect = Exception("Anthropic API Error")

        provider = ClaudeRecommendationProvider(api_key="test-key")
        incident = MagicMock()
        incident.id = 2
        incident.title = "Test"
        incident.description = "Test"
        del incident.severity
        del incident.status
        del incident.alerts
        del incident.metadata

        recommendations = provider.analyze(incident)

        assert len(recommendations) == 1
        assert recommendations[0].type == RecommendationType.GENERAL
        assert "Anthropic API Error" in recommendations[0].description

    def test_analyze_none_incident(self):
        provider = ClaudeRecommendationProvider(api_key="test-key")
        assert provider.analyze(None) == []
