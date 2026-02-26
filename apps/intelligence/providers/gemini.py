"""
Gemini (Google) intelligence provider.

Uses the Google GenAI SDK to analyze incidents.
"""

from apps.intelligence.providers.ai_base import BaseAIProvider


class GeminiRecommendationProvider(BaseAIProvider):
    """Gemini-powered intelligence provider using Google's GenAI API."""

    name = "gemini"
    description = "Gemini (Google) intelligence provider"
    default_model = "gemini-2.0-flash"

    def _call_api(self, prompt: str) -> str:
        from google import genai

        client = genai.Client(
            api_key=self.api_key,
            http_options=genai.types.HttpOptions(
                timeout=self.timeout_s * 1000
            ),  # convert seconds → ms
        )
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=self.SYSTEM_PROMPT,
                max_output_tokens=self.max_tokens,
            ),
        )
        return response.text or ""
