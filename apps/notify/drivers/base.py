"""Base driver and data structures for notification delivery.

Drivers handle sending notifications to various platforms (email, Slack, PagerDuty, etc.)
and normalize the interface for different backends.

Public API:
- NotificationMessage
- BaseNotifyDriver
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from apps.notify.templating import NotificationTemplatingService, render_template


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

    _templating_service = NotificationTemplatingService()

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

    def _render_message_templates(
        self, message: "NotificationMessage", config: dict[str, Any]
    ) -> Dict[str, Optional[str]]:
        """Render per-driver templates from config.

        Looks for keys in config:
        - 'template' or 'text_template' -> rendered plain text message
        - 'html_template' -> rendered HTML message

        Returns dict with optional 'text' and 'html' keys (values or None).
        """
        message_dict = {
            "title": message.title,
            "message": message.message,
            "severity": message.severity,
            "channel": message.channel,
            "tags": message.tags,
            "context": message.context,
        }
        return self._templating_service.render_message_templates(self.name, message_dict, config)

    def _template_context(self, message: "NotificationMessage", incident_details: dict) -> dict:
        """Build the template rendering context for message templates."""
        message_dict = {
            "title": message.title,
            "message": message.message,
            "severity": message.severity,
            "channel": message.channel,
            "tags": message.tags,
            "context": message.context,
        }
        return self._templating_service.build_template_context(message_dict, incident_details)

    def _compose_incident_details(
        self, message: "NotificationMessage", config: dict[str, Any]
    ) -> dict:
        """Compose a common incident detail payload used by all drivers.

        Returns a dict containing normalized metrics, context, summaries and
        recommendations suitable for including in driver payloads.
        """
        message_dict = {
            "title": message.title,
            "message": message.message,
            "severity": message.severity,
            "channel": message.channel,
            "tags": message.tags,
            "context": message.context,
        }
        return self._templating_service.compose_incident_details(message_dict, config)

    def _prepare_notification(self, message: "NotificationMessage", config: dict[str, Any]) -> dict:
        """Prepare rendered templates and incident details for a notification.

        Returns a dict with keys: 'incident', 'text', 'html', 'payload_obj' (if payload_template
        rendered to JSON/dict), and 'payload_raw' (string rendering when not JSON).
        """
        config = config or {}
        incident = self._compose_incident_details(message, config)
        ctx = self._template_context(message, incident)

        result: dict = {
            "incident": incident,
            "text": None,
            "html": None,
            "payload_obj": None,
            "payload_raw": None,
        }

        # Render text/html templates (this will enforce template existence per earlier logic)
        rendered = self._render_message_templates(message, config)
        result["text"] = rendered.get("text")
        result["html"] = rendered.get("html")

        # If a payload_template exists, render it and try to parse JSON
        payload_t = config.get("payload_template")
        if payload_t:
            try:
                raw = render_template(payload_t, ctx)
                if raw:
                    result["payload_raw"] = raw
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            result["payload_obj"] = parsed
                    except Exception:
                        # not JSON; leave payload_raw
                        pass
            except Exception:
                # propagate template errors
                raise

        return result
