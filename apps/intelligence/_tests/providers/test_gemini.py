"""Tests for the GeminiRecommendationProvider."""

import json
from unittest.mock import ANY, MagicMock, patch

from django.test import SimpleTestCase

from apps.intelligence.providers import RecommendationType
from apps.intelligence.providers.gemini import GeminiRecommendationProvider


class TestGeminiProviderInitialization(SimpleTestCase):
    """Tests for Gemini provider initialization."""

    def test_initialization_defaults(self):
        provider = GeminiRecommendationProvider(api_key="test-key")
        assert provider.api_key == "test-key"
        assert provider.model == "gemini-2.0-flash"
        assert provider.max_tokens == 1024

    def test_initialization_custom_values(self):
        provider = GeminiRecommendationProvider(
            api_key="custom-key", model="gemini-2.0-pro", max_tokens=2048
        )
        assert provider.api_key == "custom-key"
        assert provider.model == "gemini-2.0-pro"
        assert provider.max_tokens == 2048

    def test_provider_attributes(self):
        provider = GeminiRecommendationProvider(api_key="test-key")
        assert provider.name == "gemini"
        assert provider.description == "Gemini (Google) intelligence provider"


class TestGeminiCallApi(SimpleTestCase):
    """Tests for Gemini API calls."""

    @patch("google.genai.Client")
    def test_call_api_success(self, mock_client_class):
        mock_response = MagicMock()
        mock_response.text = '{"test": "response"}'

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client

        provider = GeminiRecommendationProvider(api_key="test-key")
        result = provider._call_api("Test prompt")

        assert result == '{"test": "response"}'
        mock_client_class.assert_called_once_with(api_key="test-key", http_options=ANY)
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.0-flash"
        assert call_kwargs["contents"] == "Test prompt"

    @patch("google.genai.Client")
    def test_call_api_custom_model(self, mock_client_class):
        mock_response = MagicMock()
        mock_response.text = "response"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client

        provider = GeminiRecommendationProvider(
            api_key="key", model="gemini-2.0-pro", max_tokens=4096
        )
        provider._call_api("prompt")

        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.0-pro"


class TestGeminiAnalyze(SimpleTestCase):
    """Tests for end-to-end analysis."""

    @patch.object(GeminiRecommendationProvider, "_call_api")
    def test_analyze_success(self, mock_call_api):
        mock_call_api.return_value = json.dumps(
            [
                {
                    "type": "disk",
                    "priority": "critical",
                    "title": "Disk Full",
                    "description": "Root partition at 95%",
                    "actions": ["Clean logs"],
                }
            ]
        )

        provider = GeminiRecommendationProvider(api_key="test-key")
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
        assert recommendations[0].type == RecommendationType.DISK
        assert recommendations[0].incident_id == 1

    @patch.object(GeminiRecommendationProvider, "_call_api")
    def test_analyze_api_error_returns_fallback(self, mock_call_api):
        mock_call_api.side_effect = Exception("Google API Error")

        provider = GeminiRecommendationProvider(api_key="test-key")
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
        assert "Google API Error" in recommendations[0].description

    def test_analyze_none_incident(self):
        provider = GeminiRecommendationProvider(api_key="test-key")
        assert provider.analyze(None) == []
