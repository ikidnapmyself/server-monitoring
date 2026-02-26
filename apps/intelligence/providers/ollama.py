"""
Ollama (local LLM) intelligence provider.

Uses the Ollama Python SDK to call a locally-hosted LLM.
"""

from typing import Any

from apps.intelligence.providers.ai_base import BaseAIProvider


class OllamaRecommendationProvider(BaseAIProvider):
    """Ollama intelligence provider for local LLM inference."""

    name = "ollama"
    description = "Ollama local LLM intelligence provider"
    default_model = "llama3.1"

    def __init__(
        self,
        host: str = "http://localhost:11434",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.host = host

    def _call_api(self, prompt: str) -> str:
        from ollama import Client

        client = Client(host=self.host, timeout=self.timeout_s)
        response = client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            options={"num_predict": self.max_tokens, "temperature": 0.3},
        )
        return response["message"]["content"]
