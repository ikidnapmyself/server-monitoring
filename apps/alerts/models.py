"""
Alert and Incident models for tracking alerts from various sources.
"""

from django.db import models
from django.utils import timezone


class AlertSeverity(models.TextChoices):
    """Severity levels for alerts."""

    CRITICAL = "critical", "Critical"
    WARNING = "warning", "Warning"
    INFO = "info", "Info"


class AlertStatus(models.TextChoices):
    """Status of an alert."""

    FIRING = "firing", "Firing"
    RESOLVED = "resolved", "Resolved"


class IncidentStatus(models.TextChoices):
    """Status of an incident."""

    OPEN = "open", "Open"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class Alert(models.Model):
    """
    Represents a single alert received from an external source.

    Alerts are typically received from monitoring systems like Prometheus AlertManager,
    Grafana, Datadog, PagerDuty, or custom sources.
    """

    # Core identification
    fingerprint = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Unique identifier for this alert (used for deduplication).",
    )
    source = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Source system that generated this alert (e.g., 'alertmanager', 'grafana').",
    )

    # Alert details
    name = models.CharField(
        max_length=255,
        help_text="Name/title of the alert.",
    )
    severity = models.CharField(
        max_length=20,
        choices=AlertSeverity.choices,
        default=AlertSeverity.WARNING,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=AlertStatus.choices,
        default=AlertStatus.FIRING,
        db_index=True,
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Detailed description of the alert.",
    )

    # Metadata
    labels = models.JSONField(
        default=dict,
        blank=True,
        help_text="Key-value labels associated with this alert.",
    )
    annotations = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional annotations (e.g., runbook URL, summary).",
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Original raw payload from the source system.",
    )

    # Correlation
    trace_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Correlation ID of the pipeline run that produced this alert.",
    )

    # Timestamps
    started_at = models.DateTimeField(
        help_text="When the alert started firing.",
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the alert was resolved (null if still firing).",
    )
    received_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When we received this alert.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    # Link to incident
    incident = models.ForeignKey(
        "Incident",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alerts",
        help_text="Incident this alert is associated with.",
    )

    # Link to originating node (agent), resolved from the instance_id label.
    node = models.ForeignKey(
        "alerts.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alerts",
        help_text="Agent this alert came from (resolved from the instance_id label).",
    )

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["source", "fingerprint"]),
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["started_at"]),
            models.Index(fields=["-received_at"]),
        ]

    def __str__(self):
        return f"[{self.severity}] {self.name} ({self.status})"

    @property
    def is_firing(self) -> bool:
        return self.status == AlertStatus.FIRING

    @property
    def duration(self):
        """Return the duration of the alert."""
        end = self.ended_at or timezone.now()
        return end - self.started_at


class Incident(models.Model):
    """
    Represents an incident created from one or more related alerts.

    Incidents group related alerts together and track their lifecycle
    from detection to resolution.
    """

    # Identification
    title = models.CharField(
        max_length=255,
        help_text="Title of the incident.",
    )

    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=IncidentStatus.choices,
        default=IncidentStatus.OPEN,
        db_index=True,
    )
    severity = models.CharField(
        max_length=20,
        choices=AlertSeverity.choices,
        default=AlertSeverity.WARNING,
        db_index=True,
    )

    # Details
    description = models.TextField(
        blank=True,
        default="",
        help_text="Description of the incident.",
    )
    summary = models.TextField(
        blank=True,
        default="",
        help_text="Summary of what happened and resolution steps.",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )
    acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # Metadata
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata for the incident.",
    )
    # The pipeline that routing matched for this incident (Phase A). Powers the
    # notify target and the journey view; SET_NULL so retiring a pipeline is safe.
    pipeline = models.ForeignKey(
        "orchestration.PipelineDefinition",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="incidents",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"[{self.status}] {self.title}"

    @property
    def is_open(self) -> bool:
        return self.status == IncidentStatus.OPEN

    @property
    def is_resolved(self) -> bool:
        return self.status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED)

    @property
    def alert_count(self) -> int:
        return self.alerts.count()

    @property
    def firing_alert_count(self) -> int:
        return self.alerts.filter(status=AlertStatus.FIRING).count()

    def acknowledge(self, save: bool = True):
        """Mark the incident as acknowledged."""
        self.status = IncidentStatus.ACKNOWLEDGED
        self.acknowledged_at = timezone.now()
        if save:
            self.save(update_fields=["status", "acknowledged_at", "updated_at"])

    def resolve(self, summary: str = "", save: bool = True):
        """Mark the incident as resolved."""
        self.status = IncidentStatus.RESOLVED
        self.resolved_at = timezone.now()
        if summary:
            self.summary = summary
        if save:
            self.save(update_fields=["status", "resolved_at", "summary", "updated_at"])

    def close(self, save: bool = True):
        """Mark the incident as closed."""
        self.status = IncidentStatus.CLOSED
        self.closed_at = timezone.now()
        if save:
            self.save(update_fields=["status", "closed_at", "updated_at"])


class AlertHistory(models.Model):
    """
    Tracks state changes and events for alerts.
    """

    alert = models.ForeignKey(
        Alert,
        on_delete=models.CASCADE,
        related_name="history",
    )

    event = models.CharField(
        max_length=50,
        help_text="Event type (e.g., 'created', 'resolved', 'escalated').",
    )
    old_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    new_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    details = models.JSONField(
        default=dict,
        blank=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Alert histories"

    def __str__(self):
        return f"{self.alert.name}: {self.event}"


class Node(models.Model):
    """A peer that has pushed cluster data to this instance (agent registry).

    Upserted on every accepted cluster push, keyed by instance_id. This is the
    queryable spine a hub uses to know its agents and that future report APIs read.
    """

    instance_id = models.CharField(max_length=255, unique=True, db_index=True)
    hostname = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    last_source = models.CharField(max_length=64, blank=True, default="")
    labels = models.JSONField(default=dict, blank=True)
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Per-checker hub-side policy, keyed by checker name, e.g. "
            '{"cpu": {"warning_threshold": 99, "critical_threshold": 99}}. '
            "Used to re-evaluate alert severity per node."
        ),
    )
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen"]
        indexes = [models.Index(fields=["-last_seen"])]

    def __str__(self):
        return f"{self.instance_id} ({self.hostname})" if self.hostname else self.instance_id

    @classmethod
    def upsert(cls, *, instance_id, hostname="", address="", source="", labels=None):
        """Create or update a Node by instance_id; refresh last_seen."""
        defaults = {"last_source": source}
        if hostname:
            defaults["hostname"] = hostname
        if address:
            defaults["address"] = address
        if labels:
            defaults["labels"] = labels
        node, created = cls.objects.update_or_create(instance_id=instance_id, defaults=defaults)
        if not created:
            node.save(update_fields=["last_seen"])  # bump auto_now
        return node
