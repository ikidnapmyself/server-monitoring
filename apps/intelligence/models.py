"""
Intelligence models.

Tracks intelligence analysis runs for audit and debugging.
"""

from django.db import models
from django.utils import timezone


class AnalysisRunStatus(models.TextChoices):
    """Status for intelligence analysis runs."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class AnalysisRun(models.Model):
    """
    Tracks individual intelligence analysis runs.

    Provides audit trail for each analysis including provider used,
    input context, output recommendations, timing, and errors.
    """

    # Correlation IDs
    trace_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Correlation ID for tracing across stages.",
    )
    pipeline_run_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Pipeline run ID this analysis belongs to.",
    )

    # Provider info
    provider = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Intelligence provider used (e.g., 'local', 'openai').",
    )
    provider_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Provider configuration (redacted).",
    )
    model_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Model name used for analysis.",
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=AnalysisRunStatus,
        default=AnalysisRunStatus.PENDING,
        db_index=True,
    )

    # Input context
    incident = models.ForeignKey(
        "alerts.Incident",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_runs",
        db_index=True,
        help_text="Incident analyzed (if applicable).",
    )
    input_summary = models.TextField(
        blank=True,
        default="",
        help_text="Summary of input provided to the analysis.",
    )
    checker_output_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reference to checker output used as input.",
    )

    # Output
    recommendations_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of recommendations generated.",
    )
    recommendations = models.JSONField(
        default=list,
        blank=True,
        help_text="List of recommendations.",
    )
    summary = models.TextField(
        blank=True,
        default="",
        help_text="Analysis summary.",
    )
    probable_cause = models.TextField(
        blank=True,
        default="",
        help_text="Probable cause identified.",
    )
    confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="Confidence score (0.0 to 1.0).",
    )

    # Token usage
    prompt_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of prompt tokens used.",
    )
    completion_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of completion tokens used.",
    )
    total_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total tokens used.",
    )

    # Error tracking
    error_message = models.TextField(
        blank=True,
        default="",
    )
    fallback_used = models.BooleanField(
        default=False,
        help_text="Whether a fallback was used instead of AI analysis.",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    duration_ms = models.FloatField(
        default=0.0,
        help_text="Analysis duration in milliseconds.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["incident", "created_at"]),
        ]

    def __str__(self):
        return f"AnalysisRun {self.trace_id} [{self.status}]"

    def mark_started(self):
        """Mark run as started."""
        self.status = AnalysisRunStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_succeeded(
        self,
        recommendations: list,
        summary: str = "",
        probable_cause: str = "",
        confidence: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ):
        """Mark run as succeeded with results."""
        self.status = AnalysisRunStatus.SUCCEEDED
        self.completed_at = timezone.now()
        self.recommendations_count = len(recommendations)
        self.recommendations = recommendations
        self.summary = summary
        self.probable_cause = probable_cause
        self.confidence = confidence
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        if prompt_tokens and completion_tokens:
            self.total_tokens = prompt_tokens + completion_tokens
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.duration_ms = delta.total_seconds() * 1000
        self.save()

    def mark_failed(self, error_message: str, fallback_used: bool = False):
        """Mark run as failed."""
        self.status = AnalysisRunStatus.FAILED
        self.completed_at = timezone.now()
        self.error_message = error_message
        self.fallback_used = fallback_used
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.duration_ms = delta.total_seconds() * 1000
        self.save()
