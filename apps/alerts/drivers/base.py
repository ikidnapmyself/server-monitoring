"""Base driver and data structures for alert ingestion.

Drivers normalize incoming alert webhook payloads from different sources
(Alertmanager, Grafana, etc.) into a common internal format.

Public API:
- ParsedAlert
- ParsedPayload
- BaseAlertDriver
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ParsedAlert:
    """Standardized alert format that all drivers produce."""

    # Required fields
    fingerprint: str
    name: str
    status: str  # "firing" or "resolved"
    started_at: datetime

    # Optional fields with defaults
    severity: str = "warning"  # "critical", "warning", "info"
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    ended_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and normalize fields after initialization."""
        # Normalize status
        self.status = (self.status or "").lower()
        if self.status not in ("firing", "resolved"):
            self.status = "firing"

        # Normalize severity
        self.severity = (self.severity or "").lower()
        if self.severity not in ("critical", "warning", "info"):
            self.severity = "warning"


@dataclass
class ParsedPayload:
    """Result of parsing an incoming webhook payload."""

    alerts: list[ParsedAlert]
    source: str

    version: str = ""
    group_key: str = ""
    receiver: str = ""
    external_url: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


class BaseAlertDriver(ABC):
    """Abstract base class for alert source drivers."""

    name: str = "base"

    @abstractmethod
    def validate(self, payload: dict[str, Any]) -> bool:
        """Validate that a payload is from this source and can be parsed."""

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> ParsedPayload:
        """Parse an incoming webhook payload into a ParsedPayload."""

    def generate_fingerprint(self, labels: dict[str, str], name: str) -> str:
        """Generate a stable fingerprint based on alert name and labels.

        Subclasses can override this for source-specific fingerprint generation.
        """
        import hashlib

        sorted_labels = sorted((labels or {}).items())
        fingerprint_str = f"{name}:{sorted_labels}"
        # Keep it short but stable for storage/display.
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]
