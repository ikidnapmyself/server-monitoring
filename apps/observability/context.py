"""ContextVars for cross-cutting log fields.

Set at three entry points (HTTP middleware, Celery signals, orchestrator
stage hook) and read by JsonLineFormatter on every record. Application
code never imports these directly.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

trace_id_var: ContextVar[str | None] = ContextVar("obs_trace_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("obs_run_id", default=None)
incident_id_var: ContextVar[int | None] = ContextVar("obs_incident_id", default=None)
stage_var: ContextVar[str | None] = ContextVar("obs_stage", default=None)
source_var: ContextVar[str | None] = ContextVar("obs_source", default=None)

_VARS: dict[str, ContextVar[Any]] = {
    "trace_id": trace_id_var,
    "run_id": run_id_var,
    "incident_id": incident_id_var,
    "stage": stage_var,
    "source": source_var,
}


@dataclass(frozen=True)
class BindToken:
    """Opaque token returned from bind(); pass to restore() to undo."""

    tokens: tuple[tuple[str, Token], ...]


def bind(**fields: Any) -> BindToken:
    """Set one or more context fields; return a token to restore on exit.

    Unknown field names raise KeyError — keeps typos from silently bloating
    the log schema.
    """
    bound: list[tuple[str, Token]] = []
    for name, value in fields.items():
        if name not in _VARS:
            # Restore anything we already bound before raising
            for n, t in bound:
                _VARS[n].reset(t)
            raise KeyError(f"unknown context field: {name!r}")
        bound.append((name, _VARS[name].set(value)))
    return BindToken(tokens=tuple(bound))


def restore(token: BindToken) -> None:
    for name, t in token.tokens:
        _VARS[name].reset(t)


def snapshot() -> dict[str, Any]:
    return {name: var.get() for name, var in _VARS.items()}
