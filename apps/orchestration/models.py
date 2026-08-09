"""
Models for pipeline orchestration.

Provides persistent state tracking for pipeline runs and stage executions.
"""

from django.db import models
from django.utils import timezone


class PipelineStage(models.TextChoices):
    """Pipeline stages in execution order."""

    INGEST = "ingest", "Ingest"
    CHECK = "check", "Check"
    ANALYZE = "analyze", "Analyze"
    NOTIFY = "notify", "Notify"


class PipelineOrigin(models.TextChoices):
    """How a pipeline run was initiated (orthogonal to which node it concerns)."""

    INCOMING_WEBHOOK = "incoming_webhook", "Incoming webhook"
    CHECKER_GENERATED = "checker_generated", "Checker generated"
    MANUAL = "manual", "Manual / CLI"


class PipelineStatus(models.TextChoices):
    """Overall pipeline status (state machine)."""

    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    INGESTED = "ingested", "Ingested"
    CHECKED = "checked", "Checked"
    ANALYZED = "analyzed", "Analyzed"
    NOTIFIED = "notified", "Notified"
    FAILED = "failed", "Failed"
    RETRYING = "retrying", "Retrying"
    SKIPPED = "skipped", "Skipped"


class StageStatus(models.TextChoices):
    """Status for individual stage executions."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    RETRYING = "retrying", "Retrying"
    SKIPPED = "skipped", "Skipped"


class PipelineRun(models.Model):
    """
    Represents a single pipeline execution.

    Tracks the full lifecycle from alert ingestion to notification,
    with correlation IDs for tracing and audit trail.
    """

    # Correlation IDs (required for tracing)
    trace_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Correlation ID for tracing across all stages and logs.",
    )
    run_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique ID for this specific pipeline run.",
    )

    # State machine
    status = models.CharField(
        max_length=20,
        choices=PipelineStatus.choices,
        default=PipelineStatus.PENDING,
        db_index=True,
    )
    current_stage = models.CharField(
        max_length=20,
        choices=PipelineStage.choices,
        null=True,
        blank=True,
        help_text="Current/last stage being executed.",
    )

    # Linked incident (nullable for non-incident triggers)
    incident = models.ForeignKey(
        "alerts.Incident",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pipeline_runs",
        help_text="Incident this pipeline run is associated with.",
    )

    # Which server this run concerns, and how it started
    node = models.ForeignKey(
        "alerts.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pipeline_runs",
        help_text="Server this run concerns (agent node, or the hub self-node).",
    )
    origin = models.CharField(
        max_length=20,
        choices=PipelineOrigin.choices,
        default=PipelineOrigin.INCOMING_WEBHOOK,
        db_index=True,
        help_text="How this run started (incoming push vs local checker vs manual).",
    )

    # Source information
    source = models.CharField(
        max_length=100,
        default="unknown",
        db_index=True,
        help_text="Source system (e.g., 'grafana', 'alertmanager', 'custom').",
    )
    alert_fingerprint = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Alert fingerprint for deduplication.",
    )
    environment = models.CharField(
        max_length=50,
        default="production",
        help_text="Environment (e.g., 'production', 'staging').",
    )

    # Payload storage (redacted references only)
    normalized_payload_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reference to normalized inbound payload (no raw secrets).",
    )
    # Raw inbound payload captured at ingest so a drain (process_inbox) can run
    # this pending run later. Durable work record — see Phase C retention note.
    inbound_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw inbound payload captured at ingest so a drain can process this run.",
    )
    checker_output_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reference to checker output.",
    )
    intelligence_output_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reference to intelligence output (prompt/response refs, redacted).",
    )
    notify_output_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reference to notification delivery results.",
    )

    # Fallback tracking
    intelligence_fallback_used = models.BooleanField(
        default=False,
        help_text="Whether the intelligence stage used a fallback (no AI analysis).",
    )

    # Retry tracking
    total_attempts = models.PositiveIntegerField(
        default=1,
        help_text="Total number of attempts for this pipeline run.",
    )
    max_retries = models.PositiveIntegerField(
        default=3,
        help_text="Maximum retries allowed for this pipeline run.",
    )

    # Error tracking
    last_error_type = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    last_error_message = models.TextField(
        blank=True,
        default="",
    )
    last_error_retryable = models.BooleanField(
        default=True,
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When pipeline execution started.",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When pipeline execution completed (success or final failure).",
    )
    total_duration_ms = models.FloatField(
        default=0.0,
        help_text="Total duration of pipeline execution in milliseconds.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["trace_id", "run_id"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["source", "alert_fingerprint"]),
        ]

    def __str__(self):
        return f"Pipeline {self.run_id} [{self.status}]"

    def mark_started(self, stage: str):
        """Mark pipeline as started with a specific stage."""
        self.current_stage = stage
        self.started_at = timezone.now()
        if self.status == PipelineStatus.PENDING:
            self.status = PipelineStatus.PENDING  # Will transition after first stage completes
        self.save(update_fields=["current_stage", "started_at", "status", "updated_at"])

    def advance_to(self, status: str, stage: str | None = None):
        """Advance pipeline to the next status and optionally update current_stage."""
        self.status = status
        if stage is not None:
            self.current_stage = stage
        self.save(update_fields=["status", "current_stage", "updated_at"])

    def mark_completed(self, status: str = PipelineStatus.NOTIFIED):
        """Mark pipeline as completed."""
        self.status = status
        self.completed_at = timezone.now()
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.total_duration_ms = delta.total_seconds() * 1000
        self.save(update_fields=["status", "completed_at", "total_duration_ms", "updated_at"])

    def mark_failed(self, error_type: str, message: str, retryable: bool = True):
        """Mark pipeline as failed."""
        self.status = PipelineStatus.FAILED
        self.completed_at = timezone.now()
        self.last_error_type = error_type
        self.last_error_message = message
        self.last_error_retryable = retryable
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.total_duration_ms = delta.total_seconds() * 1000
        self.save(
            update_fields=[
                "status",
                "completed_at",
                "total_duration_ms",
                "last_error_type",
                "last_error_message",
                "last_error_retryable",
                "updated_at",
            ]
        )

    def mark_retrying(self):
        """Mark pipeline as retrying."""
        self.status = PipelineStatus.RETRYING
        self.total_attempts += 1
        self.save(update_fields=["status", "total_attempts", "updated_at"])


class StageExecution(models.Model):
    """
    Tracks individual stage executions within a pipeline run.

    Provides detailed audit trail for each stage including timing,
    errors, retry attempts, and artifacts.
    """

    pipeline_run = models.ForeignKey(
        PipelineRun,
        on_delete=models.CASCADE,
        related_name="stage_executions",
    )

    # Stage identification
    stage = models.CharField(
        max_length=20,
        choices=PipelineStage.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=StageStatus.choices,
        default=StageStatus.PENDING,
        db_index=True,
    )
    attempt = models.PositiveIntegerField(
        default=1,
        help_text="Attempt number for this stage (1-based).",
    )

    # Idempotency
    idempotency_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Key for idempotent stage execution.",
    )

    # Input/Output references
    input_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reference to stage input data.",
    )
    output_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reference to stage output data.",
    )

    # Output snapshot (for quick access without dereferencing)
    output_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of stage output (limited size, redacted).",
    )

    # Error tracking
    error_type = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    error_message = models.TextField(
        blank=True,
        default="",
    )
    error_stack = models.TextField(
        blank=True,
        default="",
    )
    error_retryable = models.BooleanField(
        default=True,
    )

    # Timestamps
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
        help_text="Stage execution duration in milliseconds.",
    )

    class Meta:
        ordering = ["pipeline_run", "started_at"]
        indexes = [
            models.Index(fields=["pipeline_run", "stage"]),
            models.Index(fields=["stage", "status"]),
            models.Index(fields=["idempotency_key"]),
        ]
        # Allow multiple attempts per stage per pipeline run
        constraints = [
            models.UniqueConstraint(
                fields=["pipeline_run", "stage", "attempt"],
                name="unique_stage_attempt",
            ),
        ]

    def __str__(self):
        return f"{self.pipeline_run.run_id} / {self.stage} (attempt {self.attempt}) [{self.status}]"

    def mark_started(self):
        """Mark stage as started."""
        self.status = StageStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_succeeded(self, output_snapshot: dict | None = None):
        """Mark stage as succeeded."""
        self.status = StageStatus.SUCCEEDED
        self.completed_at = timezone.now()
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.duration_ms = delta.total_seconds() * 1000
        if output_snapshot:
            self.output_snapshot = output_snapshot
        self.save(update_fields=["status", "completed_at", "duration_ms", "output_snapshot"])

    def mark_failed(
        self,
        error_type: str,
        error_message: str,
        error_stack: str = "",
        retryable: bool = True,
    ):
        """Mark stage as failed."""
        self.status = StageStatus.FAILED
        self.completed_at = timezone.now()
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.duration_ms = delta.total_seconds() * 1000
        self.error_type = error_type
        self.error_message = error_message
        self.error_stack = error_stack
        self.error_retryable = retryable
        self.save(
            update_fields=[
                "status",
                "completed_at",
                "duration_ms",
                "error_type",
                "error_message",
                "error_stack",
                "error_retryable",
            ]
        )

    def mark_skipped(self, reason: str = ""):
        """Mark stage as skipped."""
        self.status = StageStatus.SKIPPED
        self.completed_at = timezone.now()
        if reason:
            self.error_message = f"Skipped: {reason}"
        self.save(update_fields=["status", "completed_at", "error_message"])


class PipelineDefinition(models.Model):
    """
    A routing pipeline: match conditions -> behaviour flags -> notify channels.

    First-match-wins by ``priority`` (see ``matches`` / ``apps.orchestration.routing``).
    The orchestrator resolves the matching pipeline for an incident after ingest and
    runs the stages its ``run_*`` flags enable.
    """

    name = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique identifier for this pipeline definition.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Human-readable description of what this pipeline does.",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number, bumped manually on significant edits.",
    )
    tags = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary tags for filtering/categorization.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this pipeline can be executed.",
    )
    created_by = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="User or system that created this definition.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- Routing spine (Phase A): flat, first-match-wins.
    # Match -> behaviour flags -> channels. ---
    match = models.JSONField(
        default=list,
        blank=True,
        help_text="Routing conditions [{field, op, value}]; empty = catch-all.",
    )
    priority = models.IntegerField(
        default=100,
        db_index=True,
        help_text="Lower is evaluated first (first match wins).",
    )
    run_checkers = models.BooleanField(default=True)
    run_intelligence = models.BooleanField(default=True)
    run_notify = models.BooleanField(default=True)
    channels = models.ManyToManyField(
        "notify.NotificationChannel", blank=True, related_name="pipelines"
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["name", "is_active"]),
            models.Index(fields=["is_active", "-updated_at"]),
            models.Index(fields=["is_active", "priority"]),
        ]

    def __str__(self):
        return f"{self.name} (v{self.version})"

    @staticmethod
    def _fact(facts: dict, field: str | None):
        if field and field.startswith("label:"):
            return (facts.get("labels") or {}).get(field.split(":", 1)[1])
        return facts.get(field)

    def matches(self, facts: dict) -> bool:
        """True if every condition holds (AND). Empty match = catch-all.

        Fails **closed**: an unknown/typoed ``op``, a non-dict condition, or an
        ``in``/``not-in`` whose ``value`` is not a list makes the condition (and so
        the pipeline) not match, rather than silently matching everything.
        """
        for cond in self.match or []:
            if not isinstance(cond, dict):
                return False
            field, op, value = cond.get("field"), cond.get("op", "is"), cond.get("value")
            actual = self._fact(facts, field)
            if op == "is":
                if actual != value:
                    return False
            elif op == "is-not":
                if actual == value:
                    return False
            elif op in ("in", "not-in"):
                if not isinstance(value, (list, tuple)):
                    return False  # membership needs a list; fail closed
                if (op == "in") == (actual not in value):
                    return False
            else:
                return False  # unknown op — fail closed
        return True
