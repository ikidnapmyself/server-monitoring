"""
Checker models for logging health check executions.

Note: Check run tracking is stored here. When a check finds an issue,
it creates an Alert via the alerts app. The orchestration layer
(PipelineRun + StageExecution) tracks the full pipeline execution.
"""

import socket

from django.db import models


class CheckStatus(models.TextChoices):
    """Status levels for check results."""

    OK = "ok", "OK"
    WARNING = "warning", "Warning"
    CRITICAL = "critical", "Critical"
    UNKNOWN = "unknown", "Unknown"


class CheckRun(models.Model):
    """
    Log of a single health check execution.

    Each time a checker runs, a CheckRun is created to record the result.
    If the check finds an issue, an Alert is created and linked here.
    """

    # Check identification
    checker_name = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Name of the checker that was run (e.g., 'cpu', 'memory', 'disk').",
    )
    hostname = models.CharField(
        max_length=255,
        default=socket.gethostname,
        db_index=True,
        help_text="Hostname where the check was executed.",
    )

    # Result
    status = models.CharField(
        max_length=20,
        choices=CheckStatus.choices,
        db_index=True,
        help_text="Result status of the check.",
    )
    message = models.TextField(
        help_text="Human-readable result message.",
    )
    metrics = models.JSONField(
        default=dict,
        blank=True,
        help_text="Measured values (e.g., {'cpu_percent': 45.2}).",
    )
    error = models.TextField(
        blank=True,
        default="",
        help_text="Error message if the check failed to execute.",
    )

    # Thresholds used
    warning_threshold = models.FloatField(
        null=True,
        blank=True,
        help_text="Warning threshold used for this check.",
    )
    critical_threshold = models.FloatField(
        null=True,
        blank=True,
        help_text="Critical threshold used for this check.",
    )

    # Link to alert (if created)
    alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="check_runs",
        help_text="Alert created from this check (if status was warning/critical).",
    )

    # Execution timing
    duration_ms = models.FloatField(
        default=0.0,
        help_text="Duration of check execution in milliseconds.",
    )
    executed_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the check was executed.",
    )

    # Correlation
    trace_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Correlation ID for pipeline tracing.",
    )

    class Meta:
        ordering = ["-executed_at"]
        indexes = [
            models.Index(fields=["checker_name", "hostname"]),
            models.Index(fields=["status", "executed_at"]),
            models.Index(fields=["checker_name", "status"]),
        ]
        verbose_name = "Check Run"
        verbose_name_plural = "Check Runs"

    def __str__(self):
        return f"[{self.status}] {self.checker_name}@{self.hostname} ({self.executed_at:%Y-%m-%d %H:%M})"

    @property
    def is_ok(self) -> bool:
        return self.status == CheckStatus.OK

    @property
    def is_critical(self) -> bool:
        return self.status == CheckStatus.CRITICAL

    @property
    def has_issue(self) -> bool:
        """Check if status indicates an issue (not OK)."""
        return self.status in (CheckStatus.WARNING, CheckStatus.CRITICAL, CheckStatus.UNKNOWN)

    @property
    def created_alert(self) -> bool:
        """Check if this run resulted in an alert."""
        return self.alert is not None
