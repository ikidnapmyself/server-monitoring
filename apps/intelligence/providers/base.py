"""
Base provider interface for intelligence providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    def analyze(self, incident: Any | None = None) -> list[Recommendation]:
        """
        Analyze system state and/or incident to generate recommendations.

        Args:
            incident: Optional incident object to analyze.

        Returns:
            List of recommendations.
        """
        ...

    @abstractmethod
    def get_recommendations(self) -> list[Recommendation]:
        """
        Get all current recommendations without a specific incident context.

        Returns:
            List of recommendations based on current system state.
        """
        ...
