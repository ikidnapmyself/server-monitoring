"""
Claude (Anthropic) intelligence provider.

Uses the Anthropic Messages API to analyze incidents.
"""

from apps.intelligence.providers.ai_base import BaseAIProvider


class ClaudeRecommendationProvider(BaseAIProvider):
    """Claude-powered intelligence provider using Anthropic's Messages API."""

    name = "claude"
    description = "Claude (Anthropic) intelligence provider"
    default_model = "claude-sonnet-4-20250514"

    def _call_api(self, prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text  # type: ignore[union-attr]
