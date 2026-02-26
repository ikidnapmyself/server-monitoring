"""Tests for the MistralRecommendationProvider."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.intelligence.providers import RecommendationType
from apps.intelligence.providers.mistral import MistralRecommendationProvider


class TestMistralProviderInitialization(SimpleTestCase):
    """Tests for Mistral provider initialization."""

    def test_initialization_defaults(self):
        provider = MistralRecommendationProvider(api_key="test-key")
        assert provider.api_key == "test-key"
        assert provider.model == "mistral-small-latest"
        assert provider.max_tokens == 1024

    def test_initialization_custom_values(self):
        provider = MistralRecommendationProvider(
            api_key="custom-key", model="mistral-large-latest", max_tokens=2048
        )
        assert provider.api_key == "custom-key"
        assert provider.model == "mistral-large-latest"
        assert provider.max_tokens == 2048

    def test_provider_attributes(self):
        provider = MistralRecommendationProvider(api_key="test-key")
        assert provider.name == "mistral"
        assert provider.description == "Mistral AI intelligence provider"


class TestMistralCallApi(SimpleTestCase):
    """Tests for Mistral API calls."""

    @patch("mistralai.Mistral")
    def test_call_api_success(self, mock_mistral_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"test": "response"}'

        mock_client = MagicMock()
        mock_client.chat.complete.return_value = mock_response
        mock_mistral_class.return_value = mock_client

        provider = MistralRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == '{"test": "response"}'
        mock_mistral_class.assert_called_once_with(api_key="test-key", timeout_ms=30000)
        mock_client.chat.complete.assert_called_once()
        call_kwargs = mock_client.chat.complete.call_args.kwargs
        assert call_kwargs["model"] == "mistral-small-latest"
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["temperature"] == 0.3
        assert len(call_kwargs["messages"]) == 2

    @patch("mistralai.Mistral")
    def test_call_api_custom_model(self, mock_mistral_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "response"

        mock_client = MagicMock()
        mock_client.chat.complete.return_value = mock_response
        mock_mistral_class.return_value = mock_client

        provider = MistralRecommendationProvider(
            api_key="key", model="mistral-large-latest", max_tokens=4096
        )
        provider._call_api("prompt")

        call_kwargs = mock_client.chat.complete.call_args.kwargs
        assert call_kwargs["model"] == "mistral-large-latest"
        assert call_kwargs["max_tokens"] == 4096

    @patch("mistralai.Mistral")
    def test_call_api_none_content_returns_empty(self, mock_mistral_class):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.complete.return_value = mock_response
        mock_mistral_class.return_value = mock_client

        provider = MistralRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == ""


class TestMistralAnalyze(SimpleTestCase):
    """Tests for end-to-end analysis."""

    @patch.object(MistralRecommendationProvider, "_call_api")
    def test_analyze_success(self, mock_call_api):
        mock_call_api.return_value = json.dumps(
            [
                {
                    "type": "general",
                    "priority": "low",
                    "title": "Configuration Issue",
                    "description": "Suboptimal configuration detected",
                    "actions": ["Update config"],
                }
            ]
        )

        provider = MistralRecommendationProvider(api_key="test-key")
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
        assert recommendations[0].type == RecommendationType.GENERAL
        assert recommendations[0].incident_id == 1

    @patch.object(MistralRecommendationProvider, "_call_api")
    def test_analyze_api_error_returns_fallback(self, mock_call_api):
        mock_call_api.side_effect = Exception("Mistral API Error")

        provider = MistralRecommendationProvider(api_key="test-key")
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
        assert "Mistral API Error" in recommendations[0].description

    def test_analyze_none_incident(self):
        provider = MistralRecommendationProvider(api_key="test-key")
        assert provider.analyze(None) == []
