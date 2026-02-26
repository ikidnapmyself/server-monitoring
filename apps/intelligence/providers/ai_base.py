"""
Base class for all LLM-backed intelligence providers.

Extracts shared prompt building, response parsing, and fallback logic
from the OpenAI provider so all AI providers share the same behavior.
"""

import json
import logging
from abc import abstractmethod
from typing import Any

from apps.intelligence.providers.base import (
    BaseProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)

logger = logging.getLogger(__name__)


class BaseAIProvider(BaseProvider):
    """Base class for all LLM-backed intelligence providers.

    Subclasses only need to implement ``_call_api`` — the actual SDK call.
    Prompt construction, JSON response parsing, and fallback handling are shared.
    """

    # Subclasses override these
    default_model: str = ""
    default_max_tokens: int = 1024
    default_timeout_s: int = 30

    SYSTEM_PROMPT = (
        "You are an expert system administrator and incident response specialist.\n"
        "Analyze the provided incident information and generate actionable recommendations.\n\n"
        "For each recommendation, provide:\n"
        "1. A type (one of: memory, disk, cpu, process, network, general)\n"
        "2. A priority (one of: low, medium, high, critical)\n"
        "3. A clear, concise title\n"
        "4. A detailed description of the issue and recommendation\n"
        "5. Specific actions to resolve the issue\n\n"
        "Respond ONLY with a JSON array of recommendations. Each recommendation should have "
        "this structure:\n"
        '{\n    "type": "memory|disk|cpu|process|network|general",\n'
        '    "priority": "low|medium|high|critical",\n'
        '    "title": "Short title",\n'
        '    "description": "Detailed description",\n'
        '    "actions": ["Action 1", "Action 2"]\n}\n\n'
        "Be specific and actionable. Focus on root cause analysis and practical "
        "remediation steps."
    )

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        max_tokens: int = 0,
        timeout_s: int = 0,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.model = model or self.default_model
        self.max_tokens = max_tokens or self.default_max_tokens
        self.timeout_s = timeout_s or self.default_timeout_s

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, incident: Any | None = None, analysis_type: str = "") -> list[Recommendation]:
        if incident is None:
            return []

        prompt = self._build_prompt(incident)
        try:
            response = self._call_api(prompt)
            return self._parse_response(response, incident_id=getattr(incident, "id", None))
        except Exception as e:
            logger.error("%s API error: %s", self.name, e)
            return self._get_fallback_recommendation(incident, str(e))

    # ------------------------------------------------------------------
    # Subclass hook
    # ------------------------------------------------------------------

    @abstractmethod
    def _call_api(self, prompt: str) -> str:
        """Make the API call and return the response text. Subclasses implement this."""
        ...  # pragma: no cover

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, incident: Any) -> str:
        parts = [
            f"Incident Title: {incident.title}",
            f"Incident Description: {incident.description}",
        ]

        if hasattr(incident, "severity"):
            parts.append(f"Severity: {incident.severity}")

        if hasattr(incident, "status"):
            parts.append(f"Status: {incident.status}")

        if hasattr(incident, "alerts"):
            alerts = incident.alerts.all()
            if alerts:
                parts.append("\nAssociated Alerts:")
                for alert in alerts[:10]:
                    alert_info = f"- {alert.name}"
                    if hasattr(alert, "description") and alert.description:
                        alert_info += f": {alert.description}"
                    parts.append(alert_info)

        if hasattr(incident, "metadata") and incident.metadata:
            parts.append(f"\nMetadata: {json.dumps(incident.metadata)}")

        return "\n".join(parts)

    def _parse_response(
        self, response: str, incident_id: int | None = None
    ) -> list[Recommendation]:
        recommendations: list[Recommendation] = []

        try:
            content = response.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            data = json.loads(content)

            if isinstance(data, dict):
                data = [data]

            for item in data:
                try:
                    rec = self._parse_recommendation_item(item, incident_id)
                    if rec:
                        recommendations.append(rec)
                except (KeyError, ValueError) as e:
                    logger.warning("Failed to parse recommendation item: %s", e)
                    continue

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse AI response as JSON: %s", e)
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
        type_map = {
            "memory": RecommendationType.MEMORY,
            "disk": RecommendationType.DISK,
            "cpu": RecommendationType.CPU,
            "process": RecommendationType.PROCESS,
            "network": RecommendationType.NETWORK,
            "general": RecommendationType.GENERAL,
        }
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
                    "Check AI provider API configuration",
                    "Consider using the local provider as fallback",
                ],
                incident_id=incident.id if hasattr(incident, "id") else None,
            )
        ]
