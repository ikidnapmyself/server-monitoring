"""Tests for the OllamaRecommendationProvider."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.intelligence.providers import RecommendationType
from apps.intelligence.providers.ollama import OllamaRecommendationProvider


class TestOllamaProviderInitialization(SimpleTestCase):
    """Tests for Ollama provider initialization."""

    def test_initialization_defaults(self):
        provider = OllamaRecommendationProvider()
        assert provider.model == "llama3.1"
        assert provider.max_tokens == 1024
        assert provider.host == "http://localhost:11434"

    def test_initialization_custom_values(self):
        provider = OllamaRecommendationProvider(
            host="http://gpu-server:11434",
            model="mixtral",
            max_tokens=2048,
        )
        assert provider.host == "http://gpu-server:11434"
        assert provider.model == "mixtral"
        assert provider.max_tokens == 2048

    def test_provider_attributes(self):
        provider = OllamaRecommendationProvider()
        assert provider.name == "ollama"
        assert provider.description == "Ollama local LLM intelligence provider"


class TestOllamaCallApi(SimpleTestCase):
    """Tests for Ollama API calls."""

    @patch("ollama.Client")
    def test_call_api_success(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"content": '{"test": "response"}'}}
        mock_client_class.return_value = mock_client

        provider = OllamaRecommendationProvider()
        result = provider._call_api("Test prompt")

        assert result == '{"test": "response"}'
        mock_client_class.assert_called_once_with(host="http://localhost:11434", timeout=30)
        mock_client.chat.assert_called_once()
        call_kwargs = mock_client.chat.call_args.kwargs
        assert call_kwargs["model"] == "llama3.1"
        assert len(call_kwargs["messages"]) == 2
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["content"] == "Test prompt"
        assert call_kwargs["options"]["num_predict"] == 1024
        assert call_kwargs["options"]["temperature"] == 0.3

    @patch("ollama.Client")
    def test_call_api_custom_host(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"content": "response"}}
        mock_client_class.return_value = mock_client

        provider = OllamaRecommendationProvider(host="http://gpu:11434")
        provider._call_api("prompt")

        mock_client_class.assert_called_once_with(host="http://gpu:11434", timeout=30)

    @patch("ollama.Client")
    def test_call_api_custom_model(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"content": "response"}}
        mock_client_class.return_value = mock_client

        provider = OllamaRecommendationProvider(model="mixtral", max_tokens=4096)
        provider._call_api("prompt")

        call_kwargs = mock_client.chat.call_args.kwargs
        assert call_kwargs["model"] == "mixtral"
        assert call_kwargs["options"]["num_predict"] == 4096


class TestOllamaAnalyze(SimpleTestCase):
    """Tests for end-to-end analysis."""

    @patch.object(OllamaRecommendationProvider, "_call_api")
    def test_analyze_success(self, mock_call_api):
        mock_call_api.return_value = json.dumps(
            [
                {
                    "type": "process",
                    "priority": "high",
                    "title": "Runaway Process",
                    "description": "Process consuming excessive resources",
                    "actions": ["Kill process"],
                }
            ]
        )

        provider = OllamaRecommendationProvider()
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
        assert recommendations[0].type == RecommendationType.PROCESS
        assert recommendations[0].incident_id == 1

    @patch.object(OllamaRecommendationProvider, "_call_api")
    def test_analyze_api_error_returns_fallback(self, mock_call_api):
        mock_call_api.side_effect = Exception("Connection refused")

        provider = OllamaRecommendationProvider()
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
        assert "Connection refused" in recommendations[0].description

    def test_analyze_none_incident(self):
        provider = OllamaRecommendationProvider()
        assert provider.analyze(None) == []
