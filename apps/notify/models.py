"""
Notification models for storing notification channel configurations.

Note: Notification delivery tracking is handled by the orchestration layer
(PipelineRun + StageExecution). This app only manages channel configuration.
"""

from django.db import models


class NotificationSeverity(models.TextChoices):
    """Severity levels for notifications."""

    CRITICAL = "critical", "Critical"
    WARNING = "warning", "Warning"
    INFO = "info", "Info"
    SUCCESS = "success", "Success"


class NotificationChannel(models.Model):
    """
    Configuration for a notification channel (e.g., Slack workspace, email list).

    Stores the driver type and configuration needed to send notifications.
    Delivery status/history is tracked in the orchestration layer (StageExecution).
    """

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Unique name for this channel (e.g., 'ops-slack', 'oncall-email').",
    )
    driver = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Driver type (e.g., 'slack', 'email', 'pagerduty', 'generic').",
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Driver-specific configuration (e.g., webhook URL, API key reference).",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this channel is active and can receive notifications.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Description of this channel's purpose.",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return f"{self.name} ({self.driver}) [{status}]"
