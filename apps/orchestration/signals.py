"""
Monitoring signals for pipeline orchestration.

App-agnostic monitoring surface used by every stage. Emits structured signals
at every stage boundary with required tags for observability.

Minimum signals per stage:
- pipeline.stage.started
- pipeline.stage.succeeded
- pipeline.stage.failed (with retryable flag)
- duration metric (stage timing)
- counters for retries and failures

Minimum tags/fields on every signal:
- trace_id/run_id
- incident_id
- stage (ingest|check|analyze|notify)
- source (grafana/alertmanager/custom)
- alert_fingerprint
- environment
- attempt
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from django.conf import settings

logger = logging.getLogger("apps.orchestration.signals")


@dataclass
class SignalTags:
    """Required tags for all monitoring signals."""

    trace_id: str
    run_id: str
    stage: str  # ingest, check, analyze, notify
    incident_id: int | None = None
    source: str = "unknown"
    alert_fingerprint: str = ""
    environment: str = "production"
    attempt: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "stage": self.stage,
            "incident_id": self.incident_id,
            "source": self.source,
            "alert_fingerprint": self.alert_fingerprint,
            "environment": self.environment,
            "attempt": self.attempt,
        }
        base.update(self.extra)
        return base


class MonitoringBackend:
    """
    Abstract monitoring backend.

    Override emit() to send signals to your preferred monitoring system.
    Default implementation logs structured JSON.
    """

    def emit(
        self,
        signal_name: str,
        tags: SignalTags,
        value: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Emit a monitoring signal."""
        raise NotImplementedError


class LoggingBackend(MonitoringBackend):
    """Default backend: structured logging."""

    def emit(
        self,
        signal_name: str,
        tags: SignalTags,
        value: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        data = {
            "signal": signal_name,
            "value": value,
            **tags.to_dict(),
            **(extra or {}),
        }
        logger.info(f"[SIGNAL] {signal_name}", extra={"signal_data": data})


class StatsdBackend(MonitoringBackend):
    """StatsD backend for metrics collection."""

    def __init__(self, host: str = "localhost", port: int = 8125, prefix: str = "pipeline"):
        self.host = host
        self.port = port
        self.prefix = prefix
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import statsd

                self._client = statsd.StatsClient(self.host, self.port, prefix=self.prefix)
            except ImportError:
                logger.warning("statsd package not installed, falling back to logging")
                return None
        return self._client

    def emit(
        self,
        signal_name: str,
        tags: SignalTags,
        value: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        client = self._get_client()
        if client is None:
            # Fallback to logging
            LoggingBackend().emit(signal_name, tags, value, extra)
            return

        # Format: prefix.signal_name.stage.source
        metric_name = f"{signal_name}.{tags.stage}.{tags.source}"

        if value is not None:
            if "duration" in signal_name or "timing" in signal_name:
                client.timing(metric_name, value)
            else:
                client.gauge(metric_name, value)
        else:
            client.incr(metric_name)


def get_monitoring_backend() -> MonitoringBackend:
    """Get configured monitoring backend."""
    backend_name = getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging")

    if backend_name == "statsd":
        host = getattr(settings, "STATSD_HOST", "localhost")
        port = getattr(settings, "STATSD_PORT", 8125)
        prefix = getattr(settings, "STATSD_PREFIX", "pipeline")
        return StatsdBackend(host=host, port=port, prefix=prefix)

    return LoggingBackend()


# Global backend instance (lazy initialized)
_backend: MonitoringBackend | None = None


def _get_backend() -> MonitoringBackend:
    global _backend
    if _backend is None:
        _backend = get_monitoring_backend()
    return _backend


def emit_stage_started(tags: SignalTags) -> None:
    """Emit signal when a stage starts execution."""
    _get_backend().emit("pipeline.stage.started", tags)


def emit_stage_succeeded(tags: SignalTags, duration_ms: float) -> None:
    """Emit signal when a stage completes successfully."""
    _get_backend().emit(
        "pipeline.stage.succeeded",
        tags,
        extra={"duration_ms": duration_ms},
    )
    _get_backend().emit("pipeline.stage.duration", tags, value=duration_ms)


def emit_stage_failed(
    tags: SignalTags,
    error_type: str,
    error_message: str,
    retryable: bool,
    duration_ms: float,
) -> None:
    """Emit signal when a stage fails."""
    _get_backend().emit(
        "pipeline.stage.failed",
        tags,
        extra={
            "error_type": error_type,
            "error_message": error_message,
            "retryable": retryable,
            "duration_ms": duration_ms,
        },
    )
    _get_backend().emit("pipeline.stage.duration", tags, value=duration_ms)
    _get_backend().emit("pipeline.stage.failure_count", tags, value=1)


def emit_stage_retrying(tags: SignalTags) -> None:
    """Emit signal when a stage is retrying."""
    _get_backend().emit("pipeline.stage.retrying", tags)
    _get_backend().emit("pipeline.stage.retry_count", tags, value=1)


def emit_pipeline_started(tags: SignalTags) -> None:
    """Emit signal when a pipeline starts."""
    _get_backend().emit("pipeline.started", tags)


def emit_pipeline_completed(tags: SignalTags, duration_ms: float, status: str) -> None:
    """Emit signal when a pipeline completes (success or failure)."""
    _get_backend().emit(
        "pipeline.completed",
        tags,
        extra={"duration_ms": duration_ms, "final_status": status},
    )
    _get_backend().emit("pipeline.duration", tags, value=duration_ms)


class StageTimer:
    """Context manager for timing stage execution."""

    def __init__(self, tags: SignalTags, on_success: Callable | None = None):
        self.tags = tags
        self.on_success = on_success
        self.start_time: float = 0.0
        self.duration_ms: float = 0.0

    def __enter__(self) -> "StageTimer":
        self.start_time = time.perf_counter()
        emit_stage_started(self.tags)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        end_time = time.perf_counter()
        self.duration_ms = (end_time - self.start_time) * 1000

        if exc_type is None:
            emit_stage_succeeded(self.tags, self.duration_ms)
            if self.on_success:
                self.on_success(self.duration_ms)
        else:
            # Determine if error is retryable
            retryable = not isinstance(exc_val, (ValueError, TypeError, KeyError))
            emit_stage_failed(
                self.tags,
                error_type=exc_type.__name__,
                error_message=str(exc_val),
                retryable=retryable,
                duration_ms=self.duration_ms,
            )
        # Don't suppress exceptions
        return False
