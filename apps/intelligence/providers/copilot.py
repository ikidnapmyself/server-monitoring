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

    def _call_api(self, prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
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
