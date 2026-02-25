"""Tests for the CopilotRecommendationProvider."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.intelligence.providers import RecommendationType
from apps.intelligence.providers.copilot import CopilotRecommendationProvider


class TestCopilotProviderInitialization(SimpleTestCase):
    """Tests for Copilot provider initialization."""

    def test_initialization_defaults(self):
        provider = CopilotRecommendationProvider(api_key="test-key")
        assert provider.api_key == "test-key"
        assert provider.model == "gpt-4o"
        assert provider.max_tokens == 1024
        assert provider.base_url == "https://api.githubcopilot.com"

    def test_initialization_custom_values(self):
        provider = CopilotRecommendationProvider(
            api_key="custom-key",
            model="gpt-4o-mini",
            max_tokens=2048,
            base_url="https://custom.endpoint.com",
        )
        assert provider.api_key == "custom-key"
        assert provider.model == "gpt-4o-mini"
        assert provider.max_tokens == 2048
        assert provider.base_url == "https://custom.endpoint.com"

    def test_provider_attributes(self):
        provider = CopilotRecommendationProvider(api_key="test-key")
        assert provider.name == "copilot"
        assert provider.description == "GitHub Copilot intelligence provider"


class TestCopilotCallApi(SimpleTestCase):
    """Tests for Copilot API calls."""

    @patch("openai.OpenAI")
    def test_call_api_success(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"test": "response"}'

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = CopilotRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == '{"test": "response"}'
        mock_openai_class.assert_called_once_with(
            api_key="test-key", base_url="https://api.githubcopilot.com"
        )
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["temperature"] == 0.3

    @patch("openai.OpenAI")
    def test_call_api_custom_base_url(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "response"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = CopilotRecommendationProvider(api_key="key", base_url="https://custom.url")
        provider._call_api("prompt")

        mock_openai_class.assert_called_once_with(api_key="key", base_url="https://custom.url")

    @patch("openai.OpenAI")
    def test_call_api_none_content_returns_empty(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = CopilotRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == ""


class TestCopilotAnalyze(SimpleTestCase):
    """Tests for end-to-end analysis."""

    @patch.object(CopilotRecommendationProvider, "_call_api")
    def test_analyze_success(self, mock_call_api):
        mock_call_api.return_value = json.dumps(
            [
                {
                    "type": "cpu",
                    "priority": "high",
                    "title": "CPU Spike",
                    "description": "CPU usage at 98%",
                    "actions": ["Kill runaway process"],
                }
            ]
        )

        provider = CopilotRecommendationProvider(api_key="test-key")
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
        assert recommendations[0].type == RecommendationType.CPU
        assert recommendations[0].incident_id == 1

    @patch.object(CopilotRecommendationProvider, "_call_api")
    def test_analyze_api_error_returns_fallback(self, mock_call_api):
        mock_call_api.side_effect = Exception("Copilot API Error")

        provider = CopilotRecommendationProvider(api_key="test-key")
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
        assert "Copilot API Error" in recommendations[0].description

    def test_analyze_none_incident(self):
        provider = CopilotRecommendationProvider(api_key="test-key")
        assert provider.analyze(None) == []
