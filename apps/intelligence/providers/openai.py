"""
OpenAI-powered intelligence provider for incident analysis and recommendations.

This provider uses OpenAI's GPT models to analyze incidents and generate
AI-driven recommendations.
"""

import json
import logging
import os
from typing import Any

from apps.intelligence.providers.base import (
    BaseProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)

logger = logging.getLogger(__name__)


class OpenAIRecommendationProvider(BaseProvider):
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

    # System prompt for incident analysis
    SYSTEM_PROMPT = """You are an expert system administrator and incident response specialist.
Analyze the provided incident information and generate actionable recommendations.

For each recommendation, provide:
1. A type (one of: memory, disk, cpu, process, network, general)
2. A priority (one of: low, medium, high, critical)
3. A clear, concise title
4. A detailed description of the issue and recommendation
5. Specific actions to resolve the issue

Respond ONLY with a JSON array of recommendations. Each recommendation should have this structure:
{
    "type": "memory|disk|cpu|process|network|general",
    "priority": "low|medium|high|critical",
    "title": "Short title",
    "description": "Detailed description",
    "actions": ["Action 1", "Action 2"]
}

Be specific and actionable. Focus on root cause analysis and practical remediation steps."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """
        Initialize the OpenAI recommendation provider.

        Args:
            api_key: OpenAI API key. Defaults to OPENAI_API_KEY env var.
            model: Model to use. Defaults to OPENAI_MODEL env var or "gpt-4o-mini".
            max_tokens: Maximum tokens for response. Defaults to OPENAI_MAX_TOKENS env var or 1024.
        """
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.max_tokens = max_tokens or int(os.environ.get("OPENAI_MAX_TOKENS", "1024"))
        self._client = None

    @property
    def client(self):
        """
        Lazy-initialize the OpenAI client.

        This avoids import errors when the openai package is not installed
        or when the provider is registered but not used.
        """
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def analyze(self, incident: Any | None = None, analysis_type: str = "") -> list[Recommendation]:
        """
        Analyze an incident using OpenAI and generate recommendations.

        Args:
            incident: An Incident object from apps.alerts.models.

        Returns:
            List of AI-generated recommendations.
        """
        if incident is None:
            return self.get_recommendations()

        prompt = self._build_prompt(incident)

        try:
            response = self._call_openai(prompt)
            return self._parse_response(response, incident_id=incident.id)
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return self._get_fallback_recommendation(incident, str(e))

    def get_recommendations(self) -> list[Recommendation]:
        """
        Get general recommendations without a specific incident context.

        For the OpenAI provider, this returns an empty list since the provider
        is designed for incident analysis. Use the local provider for
        system-state-based recommendations.

        Returns:
            Empty list (OpenAI provider requires incident context).
        """
        return []

    def _build_prompt(self, incident: Any) -> str:
        """
        Build a prompt for OpenAI from incident data.

        Args:
            incident: Incident object with title, description, and alerts.

        Returns:
            Formatted prompt string.
        """
        parts = [
            f"Incident Title: {incident.title}",
            f"Incident Description: {incident.description}",
        ]

        # Add severity if available
        if hasattr(incident, "severity"):
            parts.append(f"Severity: {incident.severity}")

        # Add status if available
        if hasattr(incident, "status"):
            parts.append(f"Status: {incident.status}")

        # Add associated alerts if available
        if hasattr(incident, "alerts"):
            alerts = incident.alerts.all()
            if alerts:
                parts.append("\nAssociated Alerts:")
                for alert in alerts[:10]:  # Limit to 10 alerts
                    alert_info = f"- {alert.name}"
                    if hasattr(alert, "description") and alert.description:
                        alert_info += f": {alert.description}"
                    parts.append(alert_info)

        # Add metadata if available
        if hasattr(incident, "metadata") and incident.metadata:
            parts.append(f"\nMetadata: {json.dumps(incident.metadata)}")

        return "\n".join(parts)

    def _call_openai(self, prompt: str) -> str:
        """
        Call the OpenAI API with the given prompt.

        Args:
            prompt: User prompt for incident analysis.

        Returns:
            Response content from OpenAI.

        Raises:
            Exception: If the API call fails.
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.max_tokens,
            temperature=0.3,  # Lower temperature for more consistent responses
        )

        return response.choices[0].message.content or ""

    def _parse_response(
        self, response: str, incident_id: int | None = None
    ) -> list[Recommendation]:
        """
        Parse OpenAI response into Recommendation objects.

        Args:
            response: JSON string from OpenAI.
            incident_id: Optional incident ID to attach to recommendations.

        Returns:
            List of parsed Recommendation objects.
        """
        recommendations = []

        try:
            # Try to extract JSON from the response
            # Handle cases where the response might have markdown code blocks
            content = response.strip()
            if content.startswith("```"):
                # Remove markdown code block markers
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            data = json.loads(content)

            # Ensure we have a list
            if isinstance(data, dict):
                data = [data]

            for item in data:
                try:
                    rec = self._parse_recommendation_item(item, incident_id)
                    if rec:
                        recommendations.append(rec)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Failed to parse recommendation item: {e}")
                    continue

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse OpenAI response as JSON: {e}")
            # Create a general recommendation from the text response
            recommendations.append(
                Recommendation(
                    type=RecommendationType.GENERAL,
                    priority=RecommendationPriority.MEDIUM,
                    title="AI Analysis",
                    description=response[:500] if response else "No analysis available",
                    actions=["Review the AI analysis above"],
                    incident_id=incident_id,
                )
            )

        return recommendations

    def _parse_recommendation_item(
        self, item: dict[str, Any], incident_id: int | None = None
    ) -> Recommendation | None:
        """
        Parse a single recommendation item from the OpenAI response.

        Args:
            item: Dictionary with recommendation data.
            incident_id: Optional incident ID.

        Returns:
            Recommendation object or None if parsing fails.
        """
        # Map type string to enum
        type_map = {
            "memory": RecommendationType.MEMORY,
            "disk": RecommendationType.DISK,
            "cpu": RecommendationType.CPU,
            "process": RecommendationType.PROCESS,
            "network": RecommendationType.NETWORK,
            "general": RecommendationType.GENERAL,
        }

        # Map priority string to enum
        priority_map = {
            "low": RecommendationPriority.LOW,
            "medium": RecommendationPriority.MEDIUM,
            "high": RecommendationPriority.HIGH,
            "critical": RecommendationPriority.CRITICAL,
        }

        rec_type = type_map.get(item.get("type", "").lower(), RecommendationType.GENERAL)
        priority = priority_map.get(item.get("priority", "").lower(), RecommendationPriority.MEDIUM)

        title = item.get("title", "AI Recommendation")
        description = item.get("description", "")
        actions = item.get("actions", [])

        if not description:
            return None

        return Recommendation(
            type=rec_type,
            priority=priority,
            title=title,
            description=description,
            actions=actions if isinstance(actions, list) else [actions],
            incident_id=incident_id,
        )

    def _get_fallback_recommendation(
        self, incident: Any, error_message: str
    ) -> list[Recommendation]:
        """
        Generate a fallback recommendation when the API call fails.

        Args:
            incident: The incident being analyzed.
            error_message: Error message from the failed API call.

        Returns:
            List with a single fallback recommendation.
        """
        return [
            Recommendation(
                type=RecommendationType.GENERAL,
                priority=RecommendationPriority.MEDIUM,
                title="AI Analysis Unavailable",
                description=(
                    f"Unable to generate AI analysis for this incident. "
                    f"Error: {error_message}. "
                    f"Please review the incident manually or try again later."
                ),
                actions=[
                    "Review incident details manually",
                    "Check OpenAI API configuration",
                    "Consider using the local provider as fallback",
                ],
                incident_id=incident.id if hasattr(incident, "id") else None,
            )
        ]
