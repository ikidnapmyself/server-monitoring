"""Base driver and data structures for notification delivery.

Drivers handle sending notifications to various platforms (email, Slack, PagerDuty, etc.)
and normalize the interface for different backends.

Public API:
- NotificationMessage
- BaseNotifyDriver
"""

from __future__ import annotations

import json
import logging
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from apps.notify.templating import NotificationTemplatingService, render_template

logger = logging.getLogger(__name__)


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

    # Common severity mappings shared across drivers
    SEVERITY_COLORS = {
        "critical": "#dc3545",
        "warning": "#ffc107",
        "info": "#17a2b8",
        "success": "#28a745",
    }

    SEVERITY_EMOJIS = {
        "critical": ":rotating_light:",
        "warning": ":warning:",
        "info": ":information_source:",
        "success": ":white_check_mark:",
    }

    PRIORITY_MAP = {
        "critical": "1",
        "warning": "2",
        "info": "3",
        "success": "3",
    }

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
        return self._templating_service.render_message_templates(
            self.name, self._message_to_dict(message), config
        )

    def _template_context(self, message: "NotificationMessage", incident_details: dict) -> dict:
        """Build the template rendering context for message templates."""
        return self._templating_service.build_template_context(
            self._message_to_dict(message), incident_details
        )

    def _compose_incident_details(
        self, message: "NotificationMessage", config: dict[str, Any]
    ) -> dict:
        """Compose a common incident detail payload used by all drivers.

        Returns a dict containing normalized metrics, context, summaries and
        recommendations suitable for including in driver payloads.
        """
        return self._templating_service.compose_incident_details(
            self._message_to_dict(message), config
        )

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

        # If a payload_template exists in config, render it and try to parse JSON
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
        elif result["text"]:
            # If no explicit payload_template, check if rendered text looks like JSON
            # This handles drivers using *_payload.j2 default templates
            stripped = result["text"].strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    parsed = json.loads(result["text"])
                    if isinstance(parsed, (dict, list)):
                        result["payload_obj"] = parsed
                        result["payload_raw"] = result["text"]
                except Exception:
                    # Not valid JSON, leave as text
                    pass

        return result

    def _message_to_dict(self, message: "NotificationMessage") -> dict[str, Any]:
        """Convert NotificationMessage to dictionary format."""
        return {
            "title": message.title,
            "message": message.message,
            "severity": message.severity,
            "channel": message.channel,
            "tags": message.tags,
            "context": message.context,
        }

    def _handle_http_error(self, e: urllib.error.HTTPError, service_name: str) -> dict[str, Any]:
        """Handle HTTP errors consistently across drivers."""
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        logger.error(f"{service_name} HTTP error {e.code}: {error_body}")
        return {"success": False, "error": f"{service_name} API error ({e.code}): {error_body}"}

    def _handle_url_error(self, e: urllib.error.URLError, service_name: str) -> dict[str, Any]:
        """Handle URL errors consistently across drivers."""
        logger.error(f"{service_name} URL error: {e.reason}")
        return {"success": False, "error": f"Failed to connect to {service_name}: {e.reason}"}

    def _handle_exception(self, e: Exception, service_name: str, action: str) -> dict[str, Any]:
        """Handle general exceptions consistently across drivers."""
        logger.exception(f"Failed to {action} {service_name}: {e}")
        return {"success": False, "error": f"Failed to {action} {service_name}: {e}"}
