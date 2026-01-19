"""
Data Transfer Objects (DTOs) for pipeline stage contracts.

Each stage returns a structured result object. These are the contracts
between stages - the orchestrator uses these to pass data down the pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StageContext:
    """
    Input context for all pipeline stages.

    This provides the stage with everything it needs to execute,
    including correlation IDs and previous stage outputs.
    """

    trace_id: str
    run_id: str
    incident_id: int | None = None
    attempt: int = 1
    environment: str = "production"
    source: str = "unknown"
    alert_fingerprint: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    previous_results: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StageError:
    """Represents an error that occurred during stage execution."""

    error_type: str
    message: str
    stack_trace: str | None = None
    retryable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IngestResult:
    """
    Result from apps.alerts (Stage 1: Ingest).

    Output:
    - incident_id: The created/updated incident ID
    - alert_fingerprint: Unique identifier for deduplication
    - severity: Alert severity level
    - source: Which monitoring system sent the alert
    - normalized_payload_ref: Reference to stored normalized payload
    """

    incident_id: int | None = None
    alert_fingerprint: str | None = None
    severity: str = "info"
    source: str = "unknown"
    normalized_payload_ref: str | None = None
    alerts_created: int = 0
    alerts_updated: int = 0
    alerts_resolved: int = 0
    incidents_created: int = 0
    incidents_updated: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckResult:
    """
    Result from apps.checkers (Stage 2: Diagnose).

    Output:
    - checks: List of check results with status and metrics
    - timings: Timing information for each check
    - errors: Any errors that occurred
    - checker_output_ref: Reference to stored checker output
    """

    checks: list[dict[str, Any]] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    timings: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    checker_output_ref: str | None = None
    duration_ms: float = 0.0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalyzeResult:
    """
    Result from apps.intelligence (Stage 3: Analyze).

    Output:
    - summary: Human-readable summary of the incident
    - probable_cause: AI-determined probable cause
    - actions: Recommended actions to take
    - confidence: Confidence score of the analysis
    - ai_output_ref: Reference to stored AI output
    - model_info: Information about the model used
    """

    summary: str = ""
    probable_cause: str = ""
    actions: list[str] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    ai_output_ref: str | None = None
    model_info: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NotifyResult:
    """
    Result from apps.notify (Stage 4: Communicate).

    Output:
    - deliveries: List of delivery results per channel
    - provider_ids: Provider-specific message IDs
    - notify_output_ref: Reference to stored notification output
    """

    deliveries: list[dict[str, Any]] = field(default_factory=list)
    provider_ids: list[str] = field(default_factory=list)
    # Prepared message payloads (not yet delivered) or message metadata created
    # by the orchestrator. Stored as generic dicts to avoid coupling to driver types.
    messages: list[dict[str, Any]] = field(default_factory=list)
    notify_output_ref: str | None = None
    channels_attempted: int = 0
    channels_succeeded: int = 0
    channels_failed: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineResult:
    """
    Final result of the entire pipeline run.

    Contains all stage results and overall status.
    """

    trace_id: str
    run_id: str
    status: str  # COMPLETED, FAILED, PARTIAL
    incident_id: int | None = None
    ingest: IngestResult | None = None
    check: CheckResult | None = None
    analyze: AnalyzeResult | None = None
    notify: NotifyResult | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_duration_ms: float = 0.0
    stages_completed: list[str] = field(default_factory=list)
    final_error: StageError | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "status": self.status,
            "incident_id": self.incident_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_duration_ms": self.total_duration_ms,
            "stages_completed": self.stages_completed,
        }
        if self.ingest:
            result["ingest"] = self.ingest.to_dict()
        if self.check:
            result["check"] = self.check.to_dict()
        if self.analyze:
            result["analyze"] = self.analyze.to_dict()
        if self.notify:
            result["notify"] = self.notify.to_dict()
        if self.final_error:
            result["final_error"] = self.final_error.to_dict()
        return result
