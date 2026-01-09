"""Base driver and data structures for notification delivery.

Drivers handle sending notifications to various platforms (email, Slack, PagerDuty, etc.)
and normalize the interface for different backends.

Public API:
- NotificationMessage
- BaseNotifyDriver
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NotificationMessage:
    """Standardized notification message format that all drivers handle."""

    # Required fields
    title: str
    message: str
    severity: str  # "critical", "warning", "info", "success"

    # Optional fields with defaults
    channel: str = "default"  # routing/destination identifier
    tags: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and normalize fields after initialization."""
        # Normalize severity
        self.severity = (self.severity or "").lower()
        if self.severity not in ("critical", "warning", "info", "success"):
            self.severity = "info"


class BaseNotifyDriver(ABC):
    """Abstract base class for notification delivery drivers."""

    name: str = "base"

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate that the driver configuration is valid."""

    @abstractmethod
    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        """Send a notification and return result metadata.

        Args:
            message: The notification message to send
            config: Driver-specific configuration

        Returns:
            Dictionary with keys like:
            - success: bool
            - message_id: str (if available)
            - error: str (if failed)
            - metadata: dict (any additional info)
        """
