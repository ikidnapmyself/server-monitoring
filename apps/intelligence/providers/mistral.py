"""
Mistral AI intelligence provider.

Uses the Mistral Python SDK to analyze incidents.
"""

from apps.intelligence.providers.ai_base import BaseAIProvider


class MistralRecommendationProvider(BaseAIProvider):
    """Mistral AI intelligence provider."""

    name = "mistral"
    description = "Mistral AI intelligence provider"
    default_model = "mistral-small-latest"

    def _call_api(self, prompt: str) -> str:
        from mistralai import (
            AssistantMessage,
            Mistral,
            SystemMessage,
            ToolMessage,
            UserMessage,
        )

        client = Mistral(api_key=self.api_key)
        messages: list[AssistantMessage | SystemMessage | ToolMessage | UserMessage] = [
            SystemMessage(content=self.SYSTEM_PROMPT),
            UserMessage(content=prompt),
        ]
        response = client.chat.complete(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.3,
            messages=messages,
        )
        content = response.choices[0].message.content
        if isinstance(content, str):
            return content
        return ""
