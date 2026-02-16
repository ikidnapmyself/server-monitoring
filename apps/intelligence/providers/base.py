"""
Base provider interface for intelligence providers.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = {"key", "secret", "token", "password", "api"}


class RecommendationType(Enum):
    """Types of recommendations."""

    MEMORY = "memory"
    DISK = "disk"
    CPU = "cpu"
    PROCESS = "process"
    NETWORK = "network"
    GENERAL = "general"


class RecommendationPriority(Enum):
    """Priority levels for recommendations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Recommendation:
    """
    A single recommendation from an intelligence provider.

    Attributes:
        type: Category of the recommendation.
        priority: How urgent this recommendation is.
        title: Short title for the recommendation.
        description: Detailed description of the issue and recommendation.
        details: Additional structured data (e.g., list of processes, files).
        actions: Suggested actions to resolve the issue.
        incident_id: Related incident ID if applicable.
    """

    type: RecommendationType
    priority: RecommendationPriority
    title: str
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    incident_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type.value,
            "priority": self.priority.value,
            "title": self.title,
            "description": self.description,
            "details": self.details,
            "actions": self.actions,
            "incident_id": self.incident_id,
        }


class BaseProvider(ABC):
    """
    Abstract base class for intelligence providers.

    Intelligence providers analyze system state and incidents to generate
    actionable recommendations.
    """

    name: str = "base"
    description: str = "Base intelligence provider"

    @abstractmethod
    def analyze(self, incident: Any | None = None, analysis_type: str = "") -> list[Recommendation]:
        """
        Analyze system state and/or incident to generate recommendations.

        Args:
            incident: Optional incident object to analyze.
            analysis_type: Optional type hint for analysis.

        Returns:
            List of recommendations.
        """
        ...

    def run(
        self,
        *,
        incident: Any | None = None,
        analysis_type: str = "",
        trace_id: str = "",
        pipeline_run_id: str = "",
        provider_config: dict | None = None,
    ) -> list[Recommendation]:
        """
        Run analysis with audit logging via AnalysisRun.

        Creates an AnalysisRun record tracking provider, status, timing,
        and recommendations. DB failures are caught and logged -- they
        never break analysis.

        Args:
            incident: Optional incident to analyze.
            analysis_type: Optional type hint for analysis.
            trace_id: Correlation ID for tracing across stages.
            pipeline_run_id: Pipeline run ID this analysis belongs to.

        Returns:
            List of recommendations from analyze().

        Raises:
            Any exception from analyze() is re-raised after marking
            the AnalysisRun as FAILED.
        """
        analysis_run = self._create_analysis_run(
            trace_id=trace_id,
            pipeline_run_id=pipeline_run_id,
            incident=incident,
            provider_config=provider_config,
        )

        if analysis_run is not None:
            try:
                analysis_run.mark_started()
            except Exception:
                logger.warning(
                    "Failed to mark AnalysisRun as started for provider=%s",
                    self.name,
                    exc_info=True,
                )
                analysis_run = None

        try:
            recommendations = self.analyze(incident, analysis_type)
        except Exception as exc:
            if analysis_run is not None:
                try:
                    analysis_run.mark_failed(str(exc))
                except Exception:
                    logger.warning(
                        "Failed to mark AnalysisRun as failed for provider=%s",
                        self.name,
                        exc_info=True,
                    )
            raise

        if analysis_run is not None:
            try:
                analysis_run.mark_succeeded(
                    recommendations=[r.to_dict() for r in recommendations],
                )
            except Exception:
                logger.warning(
                    "Failed to mark AnalysisRun as succeeded for provider=%s",
                    self.name,
                    exc_info=True,
                )

        return recommendations

    def _create_analysis_run(
        self,
        trace_id: str = "",
        pipeline_run_id: str = "",
        incident: Any | None = None,
        provider_config: dict | None = None,
    ):
        """
        Create an AnalysisRun record. Returns None on DB failure.

        Uses a lazy import to avoid circular dependencies.
        """
        try:
            from apps.intelligence.models import AnalysisRun

            return AnalysisRun.objects.create(
                trace_id=trace_id,
                pipeline_run_id=pipeline_run_id,
                provider=self.name,
                provider_config=self._redact_config(provider_config or {}),
                incident=incident,
            )
        except Exception:
            logger.warning(
                "Failed to create AnalysisRun for provider=%s",
                self.name,
                exc_info=True,
            )
            return None

    @staticmethod
    def _redact_config(config: dict[str, Any]) -> dict[str, Any]:
        """
        Redact sensitive values from provider configuration.

        Any key containing a word from SENSITIVE_PATTERNS (case-insensitive)
        will have its value replaced with '***'.

        Args:
            config: Raw provider configuration dict.

        Returns:
            New dict with sensitive values replaced.
        """
        redacted = {}
        for key, value in config.items():
            key_lower = key.lower()
            if any(pattern in key_lower for pattern in SENSITIVE_PATTERNS):
                redacted[key] = "***"
            else:
                redacted[key] = value
        return redacted
