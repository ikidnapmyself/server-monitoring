"""
GitHub Copilot intelligence provider.

Uses OpenAI SDK with GitHub Copilot's API endpoint.
"""

from typing import Any

from apps.intelligence.providers.ai_base import BaseAIProvider


class CopilotRecommendationProvider(BaseAIProvider):
    """GitHub Copilot intelligence provider (OpenAI-compatible endpoint)."""

    name = "copilot"
    description = "GitHub Copilot intelligence provider"
    default_model = "gpt-4o"

    def __init__(
        self,
        base_url: str = "https://api.githubcopilot.com",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url
        self._client = None

    @property
    def client(self):
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            from openai import OpenAI  # nosemgrep

            self._client = OpenAI(  # nosec
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_s,
            )
        return self._client

    def _call_api(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.3,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""
