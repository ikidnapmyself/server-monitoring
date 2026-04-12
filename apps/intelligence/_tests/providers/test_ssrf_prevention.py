"""SSRF prevention tests for intelligence providers."""

from unittest.mock import patch

import pytest

from config.security.url_validation import URLNotAllowedError


class TestOllamaSSRF:
    def test_rejects_private_host(self):
        with patch(
            "apps.intelligence.providers.ollama.validate_safe_url",
            side_effect=URLNotAllowedError("private"),
        ):
            from apps.intelligence.providers.ollama import OllamaRecommendationProvider

            with pytest.raises(URLNotAllowedError):
                OllamaRecommendationProvider(host="http://10.0.0.1:11434")

    def test_allows_configured_host(self):
        with patch(
            "apps.intelligence.providers.ollama.validate_safe_url",
            return_value="http://localhost:11434",
        ):
            from apps.intelligence.providers.ollama import OllamaRecommendationProvider

            provider = OllamaRecommendationProvider(host="http://localhost:11434")
            assert provider.host == "http://localhost:11434"


class TestGrokSSRF:
    def test_rejects_private_base_url(self):
        with patch(
            "apps.intelligence.providers.grok.validate_safe_url",
            side_effect=URLNotAllowedError("private"),
        ):
            from apps.intelligence.providers.grok import GrokRecommendationProvider

            with pytest.raises(URLNotAllowedError):
                GrokRecommendationProvider(base_url="http://10.0.0.1/v1")


class TestCopilotSSRF:
    def test_rejects_private_base_url(self):
        with patch(
            "apps.intelligence.providers.copilot.validate_safe_url",
            side_effect=URLNotAllowedError("private"),
        ):
            from apps.intelligence.providers.copilot import CopilotRecommendationProvider

            with pytest.raises(URLNotAllowedError):
                CopilotRecommendationProvider(base_url="http://10.0.0.1/api")
