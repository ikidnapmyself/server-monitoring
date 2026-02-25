"""
Grok (xAI) intelligence provider.

Uses OpenAI SDK with xAI's API endpoint.
"""

from typing import Any

from apps.intelligence.providers.ai_base import BaseAIProvider


class GrokRecommendationProvider(BaseAIProvider):
    """Grok intelligence provider (OpenAI-compatible xAI endpoint)."""

    name = "grok"
    description = "Grok (xAI) intelligence provider"
    default_model = "grok-3-mini"

    def __init__(
        self,
        base_url: str = "https://api.x.ai/v1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url

    def _call_api(self, prompt: str) -> str:
        from openai import OpenAI  # nosemgrep

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_s)
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.3,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""
