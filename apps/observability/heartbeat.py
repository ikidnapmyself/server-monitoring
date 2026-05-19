"""Heartbeat emitter and context manager.

Writes one JSON-line record into heartbeats.jsonl per call. Designed to
never raise — failures fall back to logger.warning so a broken heartbeat
path can never break the job it's monitoring.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

_logger = logging.getLogger("apps.observability.heartbeat")


def emit_heartbeat(
    name: str,
    status: str = "ok",
    duration_ms: float | None = None,
    metrics: dict | None = None,
) -> None:
    # Use `hb_name` instead of `name` because `name` is a reserved
    # LogRecord attribute (it's the logger name) and passing it via
    # `extra=` raises KeyError in Python's logging machinery.
    extra = {
        "hb_name": name,
        "status": status,
        "duration_ms": duration_ms,
        "metrics": metrics or {},
    }
    try:
        _logger.info("heartbeat", extra=extra)
    except Exception as exc:  # pragma: no cover (best-effort)
        # Fallback to a warning on the standard logger; never raise to the caller.
        # Wrap in its own try/except so even a broken stdlib logger can't
        # propagate up — heartbeat emission is best-effort by contract.
        try:
            logging.getLogger(__name__).warning(
                "heartbeat write failed for %s: %s",
                name,
                exc,
                extra=extra,
            )
        except Exception:
            pass


@contextmanager
def heartbeat(name: str, **metrics):
    """Wrap a job: emit `running` on enter, `ok`/`fail` on exit.

    Re-raises any exception after emitting the `fail` heartbeat.
    """
    start = time.perf_counter()
    emit_heartbeat(name, status="running")
    try:
        yield
    except Exception as exc:
        emit_heartbeat(
            name,
            status="fail",
            duration_ms=(time.perf_counter() - start) * 1000,
            metrics={"error_type": type(exc).__name__, **metrics},
        )
        raise
    else:
        emit_heartbeat(
            name,
            status="ok",
            duration_ms=(time.perf_counter() - start) * 1000,
            metrics=metrics,
        )
