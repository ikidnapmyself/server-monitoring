"""
OpenAI-powered intelligence provider for incident analysis and recommendations.

Uses OpenAI's GPT models to analyze incidents and generate AI-driven recommendations.
Extends BaseAIProvider for shared prompt/parsing logic.
"""

import os
from typing import Any

from apps.intelligence.providers.ai_base import BaseAIProvider


class OpenAIRecommendationProvider(BaseAIProvider):
    """
    OpenAI-powered intelligence provider that generates recommendations using GPT models.

    Features:
    - Analyzes incidents using GPT models
    - Generates actionable recommendations based on incident context
    - Supports custom model and token configuration
    - Lazy client initialization to avoid import errors when not using openai
    """

    name = "openai"
    description = "OpenAI-powered incident analysis"
    default_model = "gpt-4o-mini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        super().__init__(
            api_key=resolved_key or "",
            model=model or os.environ.get("OPENAI_MODEL", ""),
            max_tokens=max_tokens or int(os.environ.get("OPENAI_MAX_TOKENS", "0")),
        )
        # Preserve original behaviour: api_key=None when env var not set
        if not resolved_key:
            self.api_key = None  # type: ignore[assignment]
        self._client = None

    @property
    def client(self):
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            from openai import OpenAI  # nosemgrep

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def _call_api(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.max_tokens,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""

    # Keep backward-compat alias so existing tests that mock _call_openai still work
    def _call_openai(self, prompt: str) -> str:
        return self._call_api(prompt)
