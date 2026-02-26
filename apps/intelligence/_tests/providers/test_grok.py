"""Tests for the GrokRecommendationProvider."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.intelligence.providers import RecommendationType
from apps.intelligence.providers.grok import GrokRecommendationProvider


class TestGrokProviderInitialization(SimpleTestCase):
    """Tests for Grok provider initialization."""

    def test_initialization_defaults(self):
        provider = GrokRecommendationProvider(api_key="test-key")
        assert provider.api_key == "test-key"
        assert provider.model == "grok-3-mini"
        assert provider.max_tokens == 1024
        assert provider.base_url == "https://api.x.ai/v1"

    def test_initialization_custom_values(self):
        provider = GrokRecommendationProvider(
            api_key="custom-key",
            model="grok-3",
            max_tokens=2048,
            base_url="https://custom.xai.com/v1",
        )
        assert provider.api_key == "custom-key"
        assert provider.model == "grok-3"
        assert provider.max_tokens == 2048
        assert provider.base_url == "https://custom.xai.com/v1"

    def test_provider_attributes(self):
        provider = GrokRecommendationProvider(api_key="test-key")
        assert provider.name == "grok"
        assert provider.description == "Grok (xAI) intelligence provider"


class TestGrokCallApi(SimpleTestCase):
    """Tests for Grok API calls."""

    @patch("openai.OpenAI")
    def test_call_api_success(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"test": "response"}'

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = GrokRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == '{"test": "response"}'
        mock_openai_class.assert_called_once_with(
            api_key="test-key", base_url="https://api.x.ai/v1", timeout=30
        )
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "grok-3-mini"
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

        provider = GrokRecommendationProvider(api_key="key", base_url="https://custom.xai.com/v1")
        provider._call_api("prompt")

        mock_openai_class.assert_called_once_with(
            api_key="key", base_url="https://custom.xai.com/v1", timeout=30
        )

    @patch("openai.OpenAI")
    def test_call_api_none_content_returns_empty(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        provider = GrokRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == ""


class TestGrokAnalyze(SimpleTestCase):
    """Tests for end-to-end analysis."""

    @patch.object(GrokRecommendationProvider, "_call_api")
    def test_analyze_success(self, mock_call_api):
        mock_call_api.return_value = json.dumps(
            [
                {
                    "type": "network",
                    "priority": "medium",
                    "title": "Network Latency",
                    "description": "High latency on eth0",
                    "actions": ["Check switch"],
                }
            ]
        )

        provider = GrokRecommendationProvider(api_key="test-key")
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
        assert recommendations[0].type == RecommendationType.NETWORK
        assert recommendations[0].incident_id == 1

    @patch.object(GrokRecommendationProvider, "_call_api")
    def test_analyze_api_error_returns_fallback(self, mock_call_api):
        mock_call_api.side_effect = Exception("xAI API Error")

        provider = GrokRecommendationProvider(api_key="test-key")
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
        assert "xAI API Error" in recommendations[0].description

    def test_analyze_none_incident(self):
        provider = GrokRecommendationProvider(api_key="test-key")
        assert provider.analyze(None) == []
