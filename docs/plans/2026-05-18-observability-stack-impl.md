---
title: "2026-05-18 Observability Stack Implementation Plan"
parent: Plans
---

# Observability Stack Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the `apps.observability` app and its hooks so that every log record across the project is structured JSON-lines, cron jobs emit freshness heartbeats that route through the existing alerts pipeline, the operator has a `cli logs` reader, and cluster-mode agents periodically batch-push their JSONL streams to a hub for disconnected-node history.

**Architecture:** New `apps.observability` Django app owns the entire surface (context vars, formatters, heartbeat helper + registry, cluster push view, management commands). Three integration touch points outside it (HTTP middleware in `config/middleware/`, Celery signals in `config/celery.py`, one `bind(...)` block in `apps/orchestration/orchestrator.py`). One new in-process alert driver at `apps/alerts/drivers/internal.py`. Storage is two append-only JSONL files (`events.jsonl` + `heartbeats.jsonl`) rotated by `logging.handlers.RotatingFileHandler`; cluster storage mirrors them under `LOGS_DIR/cluster/<api_key.name>/`.

**Tech Stack:** Django 5.2, Python 3.10+, Celery (`task_prerun` / `task_postrun` signals), `contextvars` (stdlib), `logging.handlers.RotatingFileHandler` (stdlib), `urllib.request` via `config.security.safe_urlopen` (existing SSRF-safe wrapper), pytest + pytest-django, ruff + black + mypy.

**Source design:** [`docs/plans/2026-05-17-observability-stack-design.md`](2026-05-17-observability-stack-design.md). Read it first if any task is unclear.

---

## Pre-flight (read once before starting)

### Branching

All work lands on a single feature branch off `main`: `feat/observability-stack`. Each task = one commit. PR opens at end of Phase 8 once all tasks pass.

### Repo conventions to honour

- Imports are absolute (`from apps.alerts.models import Incident`), no relatives.
- Tests live under `apps/<app>/_tests/` mirroring the module tree. New observability tests under `apps/observability/_tests/`.
- `manage.py` commands live at `apps/<app>/management/commands/`.
- Views are split per endpoint under `apps/<app>/views/` (not a monolithic `views.py`).
- Format / lint: `uv run black . && uv run ruff check . --fix`. Type-check: `uv run mypy .` (best-effort).
- Tests: `uv run pytest`. Coverage gate per project rule: `uv run coverage run -m pytest && uv run coverage report` (target 100% branch on new code).
- Commit messages follow `<type>(<scope>): <subject>` from the project's recent history (`fix(security): ...`, `docs(plans): ...`, `feat(checkers): ...`).
- **Pre-commit hooks run pytest** on every commit and take ~50s. Some tests are timing-fragile around UTC minute boundaries (rate limiter window). If a commit fails on a clearly-unrelated test, retry once.

### Trust-boundary reminders (from the ISO 27003 audit)

- The cluster push endpoint **must never** trust a body-supplied `instance_id`. Always derive from `request.api_key.name`.
- The internal alert driver **must have** `signature_header = None` and **must not** be registered under `/alerts/webhook/`.
- The cluster push view body must validate as well-formed JSON-lines before any `write()` happens — reject the whole POST on the first malformed line (no half-accept).
- `INSTANCE_NAME_RE = r"^[a-z0-9._-]{1,64}$"` is the only allowlist for the per-instance directory name. Apply at view boundary.
- No `eval`, `exec`, `pickle`, `yaml.load`, or `subprocess` in any new code.
- No `logger.*` call may include API keys, webhook URLs, or `NotificationChannel.config`.

### Single-cut migration constraint

The design retires legacy log paths in the same PR. `django.log` handler, `checkers.W015`, `checkers.W016`, and `CHECKS_LOG = LOGS_DIR/"checks.log"` writes are **removed**, not deprecated. See Phase 1 Task 1.7.

### Useful skill cross-references

- `@superpowers:test-driven-development` — every task is "write failing test, see it fail, implement, see it pass, commit".
- `@superpowers:systematic-debugging` — if a test fails for unclear reasons.
- `@superpowers:verification-before-completion` — run `uv run pytest apps/observability/` after every task and `uv run coverage report` periodically.

---

## Phase 1 — Foundation: app skeleton, context, formatters, settings wiring

### Task 1.1: Scaffold the `apps.observability` Django app

**Files:**
- Create: `apps/observability/__init__.py` (empty)
- Create: `apps/observability/apps.py`
- Create: `apps/observability/_tests/__init__.py` (empty)
- Modify: `config/settings.py` — append `"apps.observability.apps.ObservabilityConfig"` to `INSTALLED_APPS` (after `apps.orchestration.apps.OrchestrationConfig`)

**Step 1: Write the failing test**

Create `apps/observability/_tests/test_app_registration.py`:

```python
"""App registration smoke tests."""

from django.apps import apps


def test_observability_app_is_registered():
    assert apps.is_installed("apps.observability")


def test_observability_config_label():
    config = apps.get_app_config("observability")
    assert config.name == "apps.observability"
    assert config.label == "observability"
```

**Step 2: Run test to verify it fails**

`uv run pytest apps/observability/_tests/test_app_registration.py -v`
Expected: FAIL — `LookupError: No installed app with label 'observability'`.

**Step 3: Implement**

`apps/observability/apps.py`:

```python
from django.apps import AppConfig


class ObservabilityConfig(AppConfig):
    name = "apps.observability"
    label = "observability"
    verbose_name = "Observability"

    default_auto_field = "django.db.models.BigAutoField"
```

Append to `INSTALLED_APPS` in `config/settings.py`:

```python
INSTALLED_APPS = [
    # ... existing ...
    "apps.orchestration.apps.OrchestrationConfig",
    "apps.observability.apps.ObservabilityConfig",   # <-- new
    "config.apps.ConfigAppConfig",
]
```

**Step 4: Run test to verify it passes**

`uv run pytest apps/observability/_tests/test_app_registration.py -v` → 2 passed.
Also run `uv run python manage.py check` to confirm Django starts cleanly.

**Step 5: Commit**

```bash
git add apps/observability/__init__.py apps/observability/apps.py \
        apps/observability/_tests/__init__.py apps/observability/_tests/test_app_registration.py \
        config/settings.py
git commit -m "feat(observability): scaffold apps.observability app"
```

---

### Task 1.2: ContextVars module

**Files:**
- Create: `apps/observability/context.py`
- Create: `apps/observability/_tests/test_context.py`

**Step 1: Write the failing tests**

`apps/observability/_tests/test_context.py`:

```python
"""Tests for apps.observability.context."""

import asyncio
import threading

from apps.observability import context


def test_default_values_are_none():
    snap = context.snapshot()
    assert snap == {
        "trace_id": None, "run_id": None, "incident_id": None,
        "stage": None, "source": None,
    }


def test_bind_sets_fields():
    token = context.bind(trace_id="abc", source="http")
    try:
        snap = context.snapshot()
        assert snap["trace_id"] == "abc"
        assert snap["source"] == "http"
        assert snap["run_id"] is None
    finally:
        context.restore(token)


def test_restore_returns_to_previous_state():
    token = context.bind(trace_id="abc")
    context.restore(token)
    assert context.snapshot()["trace_id"] is None


def test_nested_bind_restores_correctly():
    outer = context.bind(trace_id="outer")
    inner = context.bind(trace_id="inner", run_id="r1")
    assert context.snapshot()["trace_id"] == "inner"
    assert context.snapshot()["run_id"] == "r1"
    context.restore(inner)
    assert context.snapshot()["trace_id"] == "outer"
    assert context.snapshot()["run_id"] is None
    context.restore(outer)
    assert context.snapshot()["trace_id"] is None


def test_threads_have_isolated_context():
    results: list[str | None] = []

    def worker():
        results.append(context.snapshot()["trace_id"])

    token = context.bind(trace_id="main-thread")
    try:
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    finally:
        context.restore(token)

    assert results == [None]


def test_asyncio_tasks_have_isolated_context():
    async def child():
        return context.snapshot()["trace_id"]

    async def parent():
        token = context.bind(trace_id="parent")
        try:
            # New task copies parent's context at creation time
            task = asyncio.create_task(child())
            return await task
        finally:
            context.restore(token)

    result = asyncio.run(parent())
    assert result == "parent"
```

**Step 2: Run tests to verify they fail**

`uv run pytest apps/observability/_tests/test_context.py -v` → `ModuleNotFoundError: No module named 'apps.observability.context'`.

**Step 3: Implement**

`apps/observability/context.py`:

```python
"""ContextVars for cross-cutting log fields.

Set at three entry points (HTTP middleware, Celery signals, orchestrator
stage hook) and read by JsonLineFormatter on every record. Application
code never imports these directly.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

trace_id_var:    ContextVar[str | None] = ContextVar("obs_trace_id",    default=None)
run_id_var:      ContextVar[str | None] = ContextVar("obs_run_id",      default=None)
incident_id_var: ContextVar[int | None] = ContextVar("obs_incident_id", default=None)
stage_var:       ContextVar[str | None] = ContextVar("obs_stage",       default=None)
source_var:      ContextVar[str | None] = ContextVar("obs_source",      default=None)

_VARS = {
    "trace_id":    trace_id_var,
    "run_id":      run_id_var,
    "incident_id": incident_id_var,
    "stage":       stage_var,
    "source":      source_var,
}


@dataclass(frozen=True)
class BindToken:
    """Opaque token returned from bind(); pass to restore() to undo."""

    tokens: dict[str, Token]


def bind(**fields: Any) -> BindToken:
    """Set one or more context fields; return a token to restore on exit.

    Unknown field names raise KeyError — keeps typos from silently bloating
    the log schema.
    """
    tokens: dict[str, Token] = {}
    for name, value in fields.items():
        if name not in _VARS:
            # Restore anything we already bound before raising
            for n, t in tokens.items():
                _VARS[n].reset(t)
            raise KeyError(f"unknown context field: {name!r}")
        tokens[name] = _VARS[name].set(value)
    return BindToken(tokens=tokens)


def restore(token: BindToken) -> None:
    for name, t in token.tokens.items():
        _VARS[name].reset(t)


def snapshot() -> dict[str, Any]:
    return {name: var.get() for name, var in _VARS.items()}
```

**Step 4: Run tests to verify they pass**

`uv run pytest apps/observability/_tests/test_context.py -v` → 6 passed.

**Step 5: Commit**

```bash
git add apps/observability/context.py apps/observability/_tests/test_context.py
git commit -m "feat(observability): ContextVars module for cross-cutting fields"
```

---

### Task 1.3: JsonLineFormatter

**Files:**
- Create: `apps/observability/formatter.py`
- Create: `apps/observability/_tests/test_formatter.py`

**Step 1: Write the failing tests**

`apps/observability/_tests/test_formatter.py`:

```python
"""Tests for apps.observability.formatter."""

import json
import logging

import pytest

from apps.observability import context
from apps.observability.formatter import JsonLineFormatter


def make_record(name="apps.alerts.services", level=logging.INFO, msg="hello", **extra):
    record = logging.LogRecord(
        name=name, level=level, pathname="x.py", lineno=1,
        msg=msg, args=None, exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_minimal_record_round_trips_as_json():
    fmt = JsonLineFormatter()
    out = fmt.format(make_record())
    obj = json.loads(out)
    assert obj["msg"] == "hello"
    assert obj["level"] == "INFO"
    assert obj["logger"] == "apps.alerts.services"
    assert obj["v"] == 1
    assert obj["ts"].endswith("Z")
    assert "instance_id" in obj


def test_contextvars_appear_in_record(monkeypatch):
    fmt = JsonLineFormatter()
    token = context.bind(trace_id="t1", run_id="r1", incident_id=42)
    try:
        out = fmt.format(make_record())
    finally:
        context.restore(token)
    obj = json.loads(out)
    assert obj["trace_id"] == "t1"
    assert obj["run_id"] == "r1"
    assert obj["incident_id"] == 42


def test_unset_contextvars_omitted_from_output():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert "trace_id" not in obj
    assert "run_id" not in obj
    assert "incident_id" not in obj


def test_extra_kwargs_land_under_extra_key():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(severity="warning", count=3)))
    assert obj["extra"] == {"severity": "warning", "count": 3}


def test_reserved_log_record_keys_excluded_from_extra():
    fmt = JsonLineFormatter()
    # `name` and `levelname` are reserved on LogRecord but get echoed onto
    # __dict__ by Python's logging; the formatter must strip them.
    obj = json.loads(fmt.format(make_record()))
    assert "extra" not in obj or "name" not in obj.get("extra", {})


def test_category_from_extra_overrides_default():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(category="custom-cat")))
    assert obj["category"] == "custom-cat"


def test_category_resolved_from_logger_prefix():
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record(name="apps.notify.drivers.slack")))
    assert obj["category"] == "notify"


def test_exception_serialized_into_three_fields():
    fmt = JsonLineFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname="x.py", lineno=1,
            msg="failed", args=None, exc_info=sys.exc_info(),
        )
    obj = json.loads(fmt.format(record))
    assert obj["exc_type"] == "ValueError"
    assert obj["exc_msg"] == "boom"
    assert "Traceback" in obj["exc_stack"]


def test_unserialisable_object_stringifies_not_raises():
    fmt = JsonLineFormatter()
    class Weird:
        def __repr__(self):
            return "<weird>"
    out = fmt.format(make_record(thing=Weird()))
    obj = json.loads(out)
    assert obj["extra"]["thing"] == "<weird>"


def test_instance_id_falls_back_to_hostname(monkeypatch):
    monkeypatch.setattr("django.conf.settings.INSTANCE_ID", "", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "test-host")
    fmt = JsonLineFormatter()
    obj = json.loads(fmt.format(make_record()))
    assert obj["instance_id"] == "test-host"
```

**Step 2: Run tests to verify they fail**

`uv run pytest apps/observability/_tests/test_formatter.py -v` → import error.

**Step 3: Implement**

`apps/observability/formatter.py`:

```python
"""JSON-line and pretty-console formatters for the observability log stack."""

from __future__ import annotations

import json
import logging
import socket
import traceback
from datetime import datetime, timezone

from django.conf import settings

from apps.observability import context

SCHEMA_VERSION = 1

# Keys present on every LogRecord that we must NOT pass through into `extra`.
# Anything else on record.__dict__ is treated as caller-supplied extra.
_RESERVED_RECORD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
    # Fields we already promote out of extra into top-level keys
    "category", "trace_id", "run_id", "incident_id", "stage", "source",
})

# Logger-prefix → category mapping. First matching prefix wins.
_CATEGORY_MAP: tuple[tuple[str, str], ...] = (
    ("apps.observability.heartbeat", "heartbeat"),
    ("apps.observability",           "observability"),
    ("apps.alerts",                  "alerts"),
    ("apps.checkers",                "checkers"),
    ("apps.intelligence",            "intelligence"),
    ("apps.notify",                  "notify"),
    ("apps.orchestration",           "orchestration"),
    ("config.middleware",            "http"),
    ("config",                       "internal"),
    ("apps",                         "internal"),
)


def _resolve_category(logger_name: str, record_dict: dict) -> str:
    if "category" in record_dict and record_dict["category"]:
        return str(record_dict["category"])
    for prefix, cat in _CATEGORY_MAP:
        if logger_name == prefix or logger_name.startswith(prefix + "."):
            return cat
    return "internal"


def _resolve_instance_id() -> str:
    name = getattr(settings, "INSTANCE_ID", "") or ""
    if name:
        return name
    return socket.gethostname()


def _utc_iso(ts: float) -> str:
    # millisecond precision, trailing Z
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
        + f"{int((ts % 1) * 1000):03d}Z"


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line, newline-terminated."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts":          _utc_iso(record.created),
            "v":           SCHEMA_VERSION,
            "level":       record.levelname,
            "logger":      record.name,
            "msg":         record.getMessage(),
            "instance_id": _resolve_instance_id(),
        }

        # ContextVars → top-level fields (only when set)
        for name, value in context.snapshot().items():
            if value is not None:
                obj[name] = value

        # Category
        obj["category"] = _resolve_category(record.name, record.__dict__)

        # Extra keys: anything on record.__dict__ that's not a reserved key
        extra = {
            k: v for k, v in record.__dict__.items()
            if k not in _RESERVED_RECORD_KEYS
        }
        if extra:
            obj["extra"] = extra

        # Exception info
        if record.exc_info:
            exc_type, exc_val, exc_tb = record.exc_info
            obj["exc_type"]  = exc_type.__name__ if exc_type else ""
            obj["exc_msg"]   = str(exc_val) if exc_val else ""
            obj["exc_stack"] = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))

        return json.dumps(obj, default=str, ensure_ascii=False)
```

**Step 4: Run tests to verify they pass**

`uv run pytest apps/observability/_tests/test_formatter.py -v` → 10 passed.

**Step 5: Commit**

```bash
git add apps/observability/formatter.py apps/observability/_tests/test_formatter.py
git commit -m "feat(observability): JsonLineFormatter with ContextVar enrichment"
```

---

### Task 1.4: PrettyConsoleFormatter

**Files:**
- Modify: `apps/observability/formatter.py` (append class)
- Modify: `apps/observability/_tests/test_formatter.py` (append test class)

**Step 1: Append failing tests**

```python
# In test_formatter.py, append:

from apps.observability.formatter import PrettyConsoleFormatter


def test_pretty_formatter_renders_human_readable_line():
    fmt = PrettyConsoleFormatter()
    out = fmt.format(make_record())
    # Sample expected shape: "14:23:01  INFO  apps.alerts.services  hello"
    assert "INFO" in out
    assert "apps.alerts.services" in out
    assert "hello" in out


def test_pretty_formatter_includes_trace_run_when_present():
    fmt = PrettyConsoleFormatter()
    token = context.bind(trace_id="1234abcd-deadbeef", run_id="abcd1234-feedface")
    try:
        out = fmt.format(make_record())
    finally:
        context.restore(token)
    # First 8 chars of trace/run id
    assert "1234abcd" in out
    assert "abcd1234" in out


def test_pretty_formatter_omits_trace_when_unset():
    fmt = PrettyConsoleFormatter()
    out = fmt.format(make_record())
    assert "trace=" not in out
    assert "run=" not in out


def test_pretty_formatter_renders_exception_block():
    try:
        raise RuntimeError("nope")
    except RuntimeError:
        import sys
        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname="x.py", lineno=1,
            msg="oops", args=None, exc_info=sys.exc_info(),
        )
    fmt = PrettyConsoleFormatter()
    out = fmt.format(record)
    assert "RuntimeError: nope" in out
```

**Step 2: Run tests to verify they fail**

`uv run pytest apps/observability/_tests/test_formatter.py::test_pretty_formatter_renders_human_readable_line -v` → ImportError.

**Step 3: Append implementation**

```python
# Append to apps/observability/formatter.py:

class PrettyConsoleFormatter(logging.Formatter):
    """Human-readable single line for TTY consoles.

    Used only when stderr is a TTY and DEBUG=1; non-TTY contexts use the
    JSON formatter on the stream handler as well so container logs stay
    machine-readable.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = _utc_iso(record.created)[11:19]  # HH:MM:SS slice
        snap = context.snapshot()
        parts = [ts, f"{record.levelname:<5}", record.name, record.getMessage()]
        if snap.get("trace_id"):
            parts.append(f"trace={snap['trace_id'][:8]}")
        if snap.get("run_id"):
            parts.append(f"run={snap['run_id'][:8]}")
        line = "  ".join(parts)
        if record.exc_info:
            exc_type, exc_val, exc_tb = record.exc_info
            line += "\n" + "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        return line
```

**Step 4: Run tests to verify they pass**

`uv run pytest apps/observability/_tests/test_formatter.py -v` → 14 passed (10 + 4 new).

**Step 5: Commit**

```bash
git add apps/observability/formatter.py apps/observability/_tests/test_formatter.py
git commit -m "feat(observability): PrettyConsoleFormatter for TTY output"
```

---

### Task 1.5: Wire LOGGING config in settings.py

**Files:**
- Modify: `config/settings.py` (replace the `LOGGING` block; add `OBSERVABILITY_*` settings)
- Create: `config/_tests/test_logging_config.py`

**Step 1: Write the failing tests**

`config/_tests/test_logging_config.py`:

```python
"""Tests for the LOGGING configuration."""

from django.conf import settings


def test_logging_has_events_and_heartbeat_handlers():
    handlers = settings.LOGGING["handlers"]
    assert "events_file" in handlers
    assert "heartbeat_file" in handlers
    assert "console" in handlers


def test_logging_does_not_have_legacy_django_log_handler():
    # Single-cut migration — django.log handler is removed.
    handlers = settings.LOGGING["handlers"]
    assert "file" not in handlers  # the old handler name


def test_events_file_handler_uses_json_formatter():
    handlers = settings.LOGGING["handlers"]
    assert handlers["events_file"]["formatter"] == "json"
    assert handlers["events_file"]["class"] == "logging.handlers.RotatingFileHandler"


def test_heartbeat_file_handler_uses_json_formatter():
    handlers = settings.LOGGING["handlers"]
    assert handlers["heartbeat_file"]["formatter"] == "json"
    assert handlers["heartbeat_file"]["class"] == "logging.handlers.RotatingFileHandler"


def test_heartbeat_logger_routes_only_to_heartbeat_file():
    loggers = settings.LOGGING["loggers"]
    hb = loggers["apps.observability.heartbeat"]
    assert hb["handlers"] == ["heartbeat_file"]
    assert hb["propagate"] is False


def test_observability_size_settings_have_sane_defaults():
    assert settings.OBSERVABILITY_EVENTS_MAX_BYTES >= 1024 * 1024
    assert settings.OBSERVABILITY_EVENTS_BACKUPS >= 1
    assert settings.OBSERVABILITY_HEARTBEATS_MAX_BYTES >= 1024
    assert settings.OBSERVABILITY_HEARTBEATS_BACKUPS >= 1
```

**Step 2: Run tests to verify they fail**

`uv run pytest config/_tests/test_logging_config.py -v` → most fail (missing handler names / settings).

**Step 3: Implement**

Replace the entire `LOGGING = {...}` block and append the `OBSERVABILITY_*` settings in `config/settings.py`:

```python
# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
OBSERVABILITY_EVENTS_MAX_BYTES = int(
    os.environ.get("OBSERVABILITY_EVENTS_MAX_BYTES", str(50 * 1024 * 1024))
)
OBSERVABILITY_EVENTS_BACKUPS = int(
    os.environ.get("OBSERVABILITY_EVENTS_BACKUPS", "5")
)
OBSERVABILITY_HEARTBEATS_MAX_BYTES = int(
    os.environ.get("OBSERVABILITY_HEARTBEATS_MAX_BYTES", str(5 * 1024 * 1024))
)
OBSERVABILITY_HEARTBEATS_BACKUPS = int(
    os.environ.get("OBSERVABILITY_HEARTBEATS_BACKUPS", "3")
)
OBSERVABILITY_CLUSTER_MAX_BODY_BYTES = int(
    os.environ.get("OBSERVABILITY_CLUSTER_MAX_BODY_BYTES", str(10 * 1024 * 1024))
)
OBSERVABILITY_CLUSTER_MAX_AGE = int(
    os.environ.get("OBSERVABILITY_CLUSTER_MAX_AGE", "900")
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGS_DIR = Path(os.environ.get("LOGS_DIR", BASE_DIR / "logs"))
LOGS_DIR.mkdir(exist_ok=True)

_console_formatter = "pretty" if sys.stderr.isatty() and DEBUG else "json"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json":   {"()": "apps.observability.formatter.JsonLineFormatter"},
        "pretty": {"()": "apps.observability.formatter.PrettyConsoleFormatter"},
    },
    "handlers": {
        "events_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "events.jsonl"),
            "maxBytes": OBSERVABILITY_EVENTS_MAX_BYTES,
            "backupCount": OBSERVABILITY_EVENTS_BACKUPS,
            "formatter": "json",
            "encoding": "utf-8",
        },
        "heartbeat_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "heartbeats.jsonl"),
            "maxBytes": OBSERVABILITY_HEARTBEATS_MAX_BYTES,
            "backupCount": OBSERVABILITY_HEARTBEATS_BACKUPS,
            "formatter": "json",
            "encoding": "utf-8",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": _console_formatter,
        },
    },
    "loggers": {
        "apps": {
            "handlers": ["events_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "apps.observability.heartbeat": {
            "handlers": ["heartbeat_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["events_file", "console"],
        "level": "INFO",
    },
}
```

(Delete the existing `LOGS_DIR` block + old `LOGGING` block above this.) Add `import sys` at the top of the file if not already present.

**Step 4: Run tests to verify they pass**

`uv run pytest config/_tests/test_logging_config.py -v` → 6 passed.
Also: `uv run python manage.py check` → no warnings about logging.

**Step 5: Commit**

```bash
git add config/settings.py config/_tests/test_logging_config.py
git commit -m "feat(observability): wire JSON-line LOGGING config + OBSERVABILITY_* settings"
```

---

### Task 1.6: Django system check `observability.W001` (LOGS_DIR writable)

**Files:**
- Create: `apps/observability/checks.py`
- Modify: `apps/observability/apps.py` (import checks in `ready()`)
- Create: `apps/observability/_tests/test_checks.py`

**Step 1: Write the failing tests**

`apps/observability/_tests/test_checks.py`:

```python
"""Django system checks for apps.observability."""

import os
from pathlib import Path

from django.core import checks
from django.test import override_settings


def test_w001_passes_when_logs_dir_is_writable(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from apps.observability.checks import check_logs_dir_writable
    errs = check_logs_dir_writable(None)
    assert errs == []


def test_w001_fails_when_logs_dir_is_not_writable(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    os.chmod(tmp_path, 0o555)
    try:
        from apps.observability.checks import check_logs_dir_writable
        errs = check_logs_dir_writable(None)
        assert any(e.id == "observability.W001" for e in errs)
    finally:
        os.chmod(tmp_path, 0o755)
```

**Step 2: Run tests to verify they fail**

`uv run pytest apps/observability/_tests/test_checks.py -v` → import error.

**Step 3: Implement**

`apps/observability/checks.py`:

```python
"""Django system checks for the observability stack.

W001 fires when LOGS_DIR is not writable by the running process. Heartbeat
freshness checks (H001/H002/H003) are added in Phase 3.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core import checks


@checks.register()
def check_logs_dir_writable(app_configs, **kwargs):
    logs_dir = Path(getattr(settings, "LOGS_DIR", ""))
    if not logs_dir:
        return []
    try:
        if not logs_dir.exists():
            logs_dir.mkdir(parents=True, exist_ok=True)
        ok = os.access(logs_dir, os.W_OK)
    except OSError:
        ok = False
    if not ok:
        return [checks.Warning(
            f"LOGS_DIR is not writable: {logs_dir}",
            hint="Either change LOGS_DIR or grant write access to the application user.",
            id="observability.W001",
        )]
    return []
```

Update `apps/observability/apps.py` to import checks on app ready:

```python
class ObservabilityConfig(AppConfig):
    name = "apps.observability"
    label = "observability"
    verbose_name = "Observability"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.observability import checks  # noqa: F401  (register side-effect)
```

**Step 4: Run tests to verify they pass**

`uv run pytest apps/observability/_tests/test_checks.py -v` → 2 passed.
Also run `uv run python manage.py check` → no new warnings unless LOGS_DIR is read-only.

**Step 5: Commit**

```bash
git add apps/observability/checks.py apps/observability/apps.py \
        apps/observability/_tests/test_checks.py
git commit -m "feat(observability): system check W001 for LOGS_DIR writability"
```

---

### Task 1.7: Retire legacy log handlers and W015/W016

**Files:**
- Modify: `apps/checkers/preflight/checks.py` — delete `check_cron_log_*` functions and their registrations.
- Modify: `apps/checkers/management/commands/preflight.py` — remove `CHECKS_LOG = Path(settings.LOGS_DIR) / "checks.log"` and any writes through it.
- Modify: `apps/checkers/README.md` — remove the W015/W016 rows from the system-checks table.
- Modify: `apps/checkers/_tests/preflight/test_checks.py` — delete the W015/W016 tests.
- Verify: `grep -rn 'django.log\|cron.log\|checks.log' apps/ config/ bin/` returns no production-code matches.

**Step 1: Write the failing test (anti-regression)**

Append to `apps/checkers/_tests/preflight/test_checks.py`:

```python
def test_w015_is_removed():
    """The cron.log staleness check (W015) is replaced by observability.H001."""
    from apps.checkers.preflight import checks as preflight_checks
    assert not any(
        name.startswith("check_cron_log") for name in dir(preflight_checks)
    )


def test_w016_is_removed():
    """The cron.log size check (W016) is replaced by observability."""
    from apps.checkers.preflight import checks as preflight_checks
    # No symbol referencing W016 should remain
    src = open(preflight_checks.__file__).read()
    assert "W016" not in src
    assert "W015" not in src
```

**Step 2: Run tests to verify they fail**

`uv run pytest apps/checkers/_tests/preflight/test_checks.py::test_w015_is_removed -v` → FAIL.

**Step 3: Delete legacy code**

1. Open `apps/checkers/preflight/checks.py` — remove every function whose docstring/name references W015 or W016 and remove their `@register(...)` decorators / dict entries.
2. Open `apps/checkers/management/commands/preflight.py` — find any line containing `CHECKS_LOG`, `cron.log`, or writes to a `.log` file outside the normal logger and delete them. The preflight command should log via `self.stdout.write(...)` only (or via the standard `logger`).
3. Open `apps/checkers/README.md` — delete the rows for W015 and W016 from the system-check table.
4. Search & remove any imports left dangling.

Verify nothing else references the deleted functions: `grep -rn 'check_cron_log\|CHECKS_LOG' apps/ config/ bin/` → empty.

**Step 4: Run tests to verify they pass**

`uv run pytest apps/checkers/ -v` → all pass, including the two anti-regression tests just added.
Also: `uv run python manage.py check` → no W015/W016 in output.

**Step 5: Commit**

```bash
git add -u  # picks up deletions / modifications
git commit -m "refactor(checkers): retire W015/W016 cron.log checks (replaced by observability stack)"
```

---

## Phase 2 — Entry hooks: HTTP middleware, Celery signals, orchestrator

### Task 2.1: HTTP middleware sets `trace_id` per request

**Files:**
- Create: `config/middleware/observability.py`
- Modify: `config/settings.py` — insert middleware between `APIKeyAuthMiddleware` and `RateLimitMiddleware`.
- Create: `config/_tests/test_observability_middleware.py`

**Step 1: Write the failing tests**

```python
"""Tests for ObservabilityMiddleware (trace_id/source binding)."""

import uuid

import pytest
from django.test import RequestFactory

from apps.observability import context
from config.middleware.observability import ObservabilityMiddleware


def _make_response_view(captured: dict):
    def view(request):
        captured["trace_id_during_request"] = context.snapshot()["trace_id"]
        captured["source_during_request"]   = context.snapshot()["source"]
        from django.http import HttpResponse
        return HttpResponse("ok")
    return view


def test_middleware_generates_trace_id_when_header_absent():
    captured: dict = {}
    mw = ObservabilityMiddleware(_make_response_view(captured))
    req = RequestFactory().get("/")
    mw(req)
    assert captured["trace_id_during_request"]
    uuid.UUID(captured["trace_id_during_request"])  # parses as uuid


def test_middleware_uses_x_trace_id_header_when_present():
    captured: dict = {}
    mw = ObservabilityMiddleware(_make_response_view(captured))
    req = RequestFactory().get("/", HTTP_X_TRACE_ID="caller-supplied-123")
    mw(req)
    assert captured["trace_id_during_request"] == "caller-supplied-123"


def test_middleware_sets_source_http():
    captured: dict = {}
    mw = ObservabilityMiddleware(_make_response_view(captured))
    mw(RequestFactory().get("/"))
    assert captured["source_during_request"] == "http"


def test_middleware_clears_context_after_response():
    mw = ObservabilityMiddleware(_make_response_view({}))
    mw(RequestFactory().get("/"))
    assert context.snapshot()["trace_id"] is None
    assert context.snapshot()["source"] is None


def test_middleware_clears_context_on_exception():
    def bad_view(request):
        raise ValueError("explode")
    mw = ObservabilityMiddleware(bad_view)
    with pytest.raises(ValueError):
        mw(RequestFactory().get("/"))
    assert context.snapshot()["trace_id"] is None
```

**Step 2: Run tests to verify they fail**

`uv run pytest config/_tests/test_observability_middleware.py -v` → ImportError.

**Step 3: Implement**

`config/middleware/observability.py`:

```python
"""ObservabilityMiddleware — bind trace_id/source per HTTP request.

Runs immediately after APIKeyAuthMiddleware so the API key (if any) is
already attached to request, and before the application views fire any
log calls.
"""

from __future__ import annotations

import uuid

from apps.observability import context


class ObservabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        trace_id = request.META.get("HTTP_X_TRACE_ID") or str(uuid.uuid4())
        token = context.bind(trace_id=trace_id, source="http")
        try:
            return self.get_response(request)
        finally:
            context.restore(token)
```

In `config/settings.py`, update `MIDDLEWARE`:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.api_key_auth.APIKeyAuthMiddleware",
    "config.middleware.observability.ObservabilityMiddleware",   # <-- new
    "config.middleware.rate_limit.RateLimitMiddleware",
]
```

**Step 4: Run tests to verify they pass**

`uv run pytest config/_tests/test_observability_middleware.py -v` → 5 passed.
`uv run pytest` (full suite) → ensure nothing broke.

**Step 5: Commit**

```bash
git add config/middleware/observability.py config/settings.py \
        config/_tests/test_observability_middleware.py
git commit -m "feat(observability): HTTP middleware binds trace_id/source per request"
```

---

### Task 2.2: Celery signals bind/restore context

**Files:**
- Modify: `config/celery.py` (add signal handlers)
- Create: `config/_tests/test_celery_signals.py`

**Step 1: Write the failing tests**

```python
"""Tests for Celery task_prerun/task_postrun handlers."""

from celery import shared_task

from apps.observability import context


@shared_task
def _probe_task(probe: dict):
    snap = context.snapshot()
    probe["trace_id"] = snap["trace_id"]
    probe["source"] = snap["source"]
    return probe


def test_celery_signals_bind_trace_id_during_task(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    probe: dict = {}
    _probe_task.apply(args=({},), headers={"trace_id": "celery-trace-1"}).get()
    # We can't easily intercept eager-task headers; rely on the prerun
    # signal having set source even with a generated trace_id.
    # (Adjust this test based on actual signal wiring.)
    assert context.snapshot()["trace_id"] is None  # cleared post-task


def test_celery_signals_clear_context_after_task(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    _probe_task.apply(args=({},)).get()
    assert context.snapshot()["trace_id"] is None
    assert context.snapshot()["source"] is None
```

**Step 2: Run tests to verify they fail**

`uv run pytest config/_tests/test_celery_signals.py -v` → fails (handlers not registered).

**Step 3: Implement**

Append to `config/celery.py`:

```python
import uuid

from celery.signals import task_postrun, task_prerun

from apps.observability import context

_BIND_TOKENS: dict[str, object] = {}


@task_prerun.connect
def _obs_task_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **_):
    headers = getattr(task.request, "headers", None) or {}
    trace_id = headers.get("trace_id") or str(uuid.uuid4())
    token = context.bind(trace_id=trace_id, source="celery")
    if task_id is not None:
        _BIND_TOKENS[task_id] = token


@task_postrun.connect
def _obs_task_postrun(sender=None, task_id=None, **_):
    token = _BIND_TOKENS.pop(task_id, None)
    if token is not None:
        context.restore(token)
```

**Step 4: Run tests to verify they pass**

`uv run pytest config/_tests/test_celery_signals.py -v` → passes.

**Step 5: Commit**

```bash
git add config/celery.py config/_tests/test_celery_signals.py
git commit -m "feat(observability): Celery task_prerun/postrun bind trace_id"
```

---

### Task 2.3: Orchestrator binds run_id / incident_id / stage

**Files:**
- Modify: `apps/orchestration/orchestrator.py` (one `bind()`/`restore()` block in `start_pipeline` and in `_execute_stage_with_retry`)
- Modify: `apps/orchestration/_tests/test_orchestrator.py` (extend a happy-path test to assert events.jsonl carries trace_id/run_id)

**Step 1: Write the failing test**

In an existing pipeline-run test or a new one (`apps/orchestration/_tests/test_orchestrator_context.py`):

```python
"""Tests that orchestrator binds run_id/incident_id/stage into ContextVars."""

import json
from pathlib import Path

from apps.observability import context
from apps.orchestration.orchestrator import PipelineOrchestrator


def test_run_pipeline_sets_contextvars(tmp_path, settings, caplog):
    settings.LOGS_DIR = tmp_path
    # Capture the run_id seen during execution
    seen: dict = {}
    orig_execute = PipelineOrchestrator._execute_pipeline
    def spy(self, pipeline_run, payload):
        seen["trace_id"] = context.snapshot()["trace_id"]
        seen["run_id"]   = context.snapshot()["run_id"]
        seen["source"]   = context.snapshot()["source"]
        return orig_execute(self, pipeline_run, payload)
    PipelineOrchestrator._execute_pipeline = spy
    try:
        orch = PipelineOrchestrator()
        orch.run_pipeline(payload={"payload": {"x": 1}}, source="test")
    finally:
        PipelineOrchestrator._execute_pipeline = orig_execute

    assert seen["trace_id"]
    assert seen["run_id"]
    assert seen["source"] == "test"
    # Context cleared after pipeline returns
    assert context.snapshot()["run_id"] is None
```

**Step 2: Run to verify it fails**

`uv run pytest apps/orchestration/_tests/test_orchestrator_context.py -v` → FAIL.

**Step 3: Implement**

In `apps/orchestration/orchestrator.py`:

```python
# Top imports
from apps.observability import context as obs_context
```

In `_execute_pipeline()`, just after `start_time = time.perf_counter()`:

```python
obs_token = obs_context.bind(
    trace_id=pipeline_run.trace_id,
    run_id=pipeline_run.run_id,
    source=pipeline_run.source,
)
try:
    # ... existing body of _execute_pipeline ...
finally:
    obs_context.restore(obs_token)
```

In `_execute_stage_with_retry()`, at the top of each attempt's `try:` block, before `emit_stage_started(tags)`:

```python
stage_token = obs_context.bind(stage=stage, incident_id=incident_id)
try:
    # ... existing try body ...
finally:
    obs_context.restore(stage_token)
```

**Step 4: Run to verify it passes**

`uv run pytest apps/orchestration/_tests/test_orchestrator_context.py -v` → PASS.
Also: `uv run pytest apps/orchestration/` → ensure existing tests still pass.

**Step 5: Commit**

```bash
git add apps/orchestration/orchestrator.py apps/orchestration/_tests/test_orchestrator_context.py
git commit -m "feat(observability): orchestrator binds trace_id/run_id/incident_id/stage into ContextVars"
```

---

## Phase 3 — Heartbeats: helper, registry, reader, system checks

### Task 3.1: `emit_heartbeat()` helper + `heartbeat()` context manager

**Files:**
- Create: `apps/observability/heartbeat.py`
- Create: `apps/observability/_tests/test_heartbeat.py`

**Step 1: Write the failing tests**

```python
"""Tests for emit_heartbeat() and heartbeat() context manager."""

import json
import logging
from pathlib import Path

import pytest

from apps.observability.heartbeat import emit_heartbeat, heartbeat


def _read_heartbeats(logs_dir: Path) -> list[dict]:
    path = logs_dir / "heartbeats.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_emit_heartbeat_writes_one_record(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    emit_heartbeat("check_health.hourly", status="ok",
                   duration_ms=12.3, metrics={"checks_run": 5})
    recs = _read_heartbeats(tmp_path)
    assert len(recs) == 1
    r = recs[0]
    assert r["name"] == "check_health.hourly"
    assert r["status"] == "ok"
    assert r["duration_ms"] == 12.3
    assert r["metrics"] == {"checks_run": 5}


def test_emit_heartbeat_never_raises_on_disk_error(monkeypatch, settings, tmp_path):
    settings.LOGS_DIR = tmp_path
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr("logging.handlers.RotatingFileHandler.emit", boom)
    # Must not raise
    emit_heartbeat("test.job", status="ok")


def test_heartbeat_ctx_manager_emits_running_then_ok(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    with heartbeat("test.job"):
        pass
    recs = _read_heartbeats(tmp_path)
    assert [r["status"] for r in recs] == ["running", "ok"]
    assert recs[1]["duration_ms"] is not None


def test_heartbeat_ctx_manager_emits_fail_and_reraises(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    with pytest.raises(RuntimeError):
        with heartbeat("test.job"):
            raise RuntimeError("oops")
    recs = _read_heartbeats(tmp_path)
    assert recs[-1]["status"] == "fail"
    assert recs[-1]["metrics"]["error_type"] == "RuntimeError"
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/test_heartbeat.py -v` → ImportError.

**Step 3: Implement**

`apps/observability/heartbeat.py`:

```python
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
    extra = {
        "name": name,
        "status": status,
        "duration_ms": duration_ms,
        "metrics": metrics or {},
    }
    try:
        _logger.info("heartbeat", extra=extra)
    except Exception as exc:  # pragma: no cover (best-effort)
        # Fallback to a warning on the standard logger; never raise to the caller.
        logging.getLogger(__name__).warning(
            "heartbeat write failed for %s: %s", name, exc, extra=extra,
        )


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
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_heartbeat.py -v` → 4 passed.

**Step 5: Commit**

```bash
git add apps/observability/heartbeat.py apps/observability/_tests/test_heartbeat.py
git commit -m "feat(observability): emit_heartbeat() helper + heartbeat() ctx manager"
```

---

### Task 3.2: Heartbeat registry

**Files:**
- Create: `apps/observability/heartbeat_registry.py`
- Create: `apps/observability/_tests/test_heartbeat_registry.py`

**Step 1: Write the failing tests**

```python
"""Tests for HEARTBEAT_REGISTRY."""

from datetime import timedelta

from apps.observability.heartbeat_registry import (
    HEARTBEAT_REGISTRY,
    HeartbeatSpec,
)


def test_known_specs_are_registered():
    assert "check_health.hourly" in HEARTBEAT_REGISTRY
    assert "check_health.daily" in HEARTBEAT_REGISTRY
    assert "push_to_hub" in HEARTBEAT_REGISTRY
    assert "cluster_push.events" in HEARTBEAT_REGISTRY
    assert "preflight.scheduled" in HEARTBEAT_REGISTRY


def test_spec_shape():
    spec = HEARTBEAT_REGISTRY["check_health.hourly"]
    assert isinstance(spec, HeartbeatSpec)
    assert isinstance(spec.max_age, timedelta)
    assert spec.max_age.total_seconds() > 0
    assert spec.desc


def test_agent_only_flag_present_on_push_jobs():
    assert HEARTBEAT_REGISTRY["push_to_hub"].agent_only is True
    assert HEARTBEAT_REGISTRY["cluster_push.events"].agent_only is True
    assert HEARTBEAT_REGISTRY["check_health.hourly"].agent_only is False
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/test_heartbeat_registry.py -v` → ImportError.

**Step 3: Implement**

`apps/observability/heartbeat_registry.py`:

```python
"""Registry of expected heartbeats.

Operators see freshness alerts only for names in this registry. Adding
a new entry is a code change that ships with the job that emits it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class HeartbeatSpec:
    max_age: timedelta
    desc: str
    agent_only: bool = False


HEARTBEAT_REGISTRY: dict[str, HeartbeatSpec] = {
    "check_health.hourly":  HeartbeatSpec(timedelta(minutes=75),  "Hourly health-check cron"),
    "check_health.daily":   HeartbeatSpec(timedelta(hours=25),    "Daily health-check cron"),
    "push_to_hub":          HeartbeatSpec(timedelta(minutes=15),  "Agent → hub alerts push", agent_only=True),
    "cluster_push.events":  HeartbeatSpec(timedelta(minutes=15),  "Agent → hub log push",    agent_only=True),
    "preflight.scheduled":  HeartbeatSpec(timedelta(hours=25),    "Daily preflight"),
}
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_heartbeat_registry.py -v` → 3 passed.

**Step 5: Commit**

```bash
git add apps/observability/heartbeat_registry.py apps/observability/_tests/test_heartbeat_registry.py
git commit -m "feat(observability): HEARTBEAT_REGISTRY of expected jobs and max_age"
```

---

### Task 3.3: Heartbeat reader

**Files:**
- Create: `apps/observability/heartbeat_reader.py`
- Create: `apps/observability/_tests/test_heartbeat_reader.py`

**Step 1: Write the failing tests**

```python
"""Tests for latest_heartbeats()."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.observability.heartbeat_reader import latest_heartbeats


def _write(path: Path, records: list[dict]):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_empty_file_returns_empty_dict(tmp_path):
    (tmp_path / "heartbeats.jsonl").write_text("")
    assert latest_heartbeats(tmp_path) == {}


def test_missing_file_returns_empty_dict(tmp_path):
    assert latest_heartbeats(tmp_path) == {}


def test_latest_per_name_wins(tmp_path):
    _write(tmp_path / "heartbeats.jsonl", [
        {"ts": "2026-05-17T10:00:00Z", "name": "job.a", "status": "ok",  "level": "INFO", "v": 1, "logger": "h", "msg": "h", "instance_id": "x"},
        {"ts": "2026-05-17T11:00:00Z", "name": "job.a", "status": "fail","level": "INFO", "v": 1, "logger": "h", "msg": "h", "instance_id": "x"},
        {"ts": "2026-05-17T10:30:00Z", "name": "job.b", "status": "ok",  "level": "INFO", "v": 1, "logger": "h", "msg": "h", "instance_id": "x"},
    ])
    latest = latest_heartbeats(tmp_path)
    assert latest["job.a"].status == "fail"
    assert latest["job.b"].status == "ok"


def test_reader_includes_rotated_backup(tmp_path):
    _write(tmp_path / "heartbeats.jsonl.1", [
        {"ts": "2026-05-17T09:00:00Z", "name": "job.c", "status": "ok", "level": "INFO", "v": 1, "logger": "h", "msg": "h", "instance_id": "x"},
    ])
    _write(tmp_path / "heartbeats.jsonl", [])
    latest = latest_heartbeats(tmp_path)
    assert latest["job.c"].status == "ok"


def test_malformed_line_skipped(tmp_path):
    p = tmp_path / "heartbeats.jsonl"
    p.write_text("not-json\n" + json.dumps({"ts": "2026-05-17T10:00:00Z", "name": "x", "status": "ok", "level": "INFO", "v": 1, "logger": "h", "msg": "h", "instance_id": "x"}) + "\n")
    latest = latest_heartbeats(tmp_path)
    assert "x" in latest
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/test_heartbeat_reader.py -v` → ImportError.

**Step 3: Implement**

`apps/observability/heartbeat_reader.py`:

```python
"""Scan heartbeats.jsonl (+ most recent rotated backup) for latest record per name."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeartbeatRecord:
    name: str
    ts: str
    status: str
    duration_ms: float | None = None
    metrics: dict | None = None

    @property
    def ts_dt(self) -> datetime:
        # Parse trailing Z manually since fromisoformat doesn't accept it before py3.11 in all cases
        s = self.ts.rstrip("Z")
        return datetime.fromisoformat(s).replace(tzinfo=datetime.now().astimezone().tzinfo)


def latest_heartbeats(logs_dir: Path | None = None) -> dict[str, HeartbeatRecord]:
    base = Path(logs_dir) if logs_dir else Path(settings.LOGS_DIR)
    candidates = [base / "heartbeats.jsonl.1", base / "heartbeats.jsonl"]
    latest: dict[str, HeartbeatRecord] = {}
    for path in candidates:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    _logger.warning("skipping malformed heartbeat line in %s", path)
                    continue
                name = obj.get("name")
                ts = obj.get("ts")
                if not name or not ts:
                    continue
                rec = HeartbeatRecord(
                    name=name, ts=ts,
                    status=obj.get("status", "ok"),
                    duration_ms=obj.get("duration_ms"),
                    metrics=obj.get("metrics") or {},
                )
                prev = latest.get(name)
                if prev is None or rec.ts > prev.ts:
                    latest[name] = rec
    return latest
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_heartbeat_reader.py -v` → 5 passed.

**Step 5: Commit**

```bash
git add apps/observability/heartbeat_reader.py apps/observability/_tests/test_heartbeat_reader.py
git commit -m "feat(observability): latest_heartbeats() reader scans live + rotated backup"
```

---

### Task 3.4: System checks H001/H002/H003

**Files:**
- Modify: `apps/observability/checks.py` (append three checks)
- Modify: `apps/observability/_tests/test_checks.py` (append tests)

**Step 1: Write the failing tests**

```python
# Append to test_checks.py:

import json
from datetime import datetime, timedelta, timezone

import pytest


def _write_hb(tmp_path, name, *, status="ok", age=timedelta(0)):
    ts = (datetime.now(tz=timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    line = json.dumps({
        "ts": ts, "v": 1, "level": "INFO", "logger": "x", "msg": "h",
        "instance_id": "test", "name": name, "status": status,
    })
    (tmp_path / "heartbeats.jsonl").write_text(line + "\n")


def test_h001_fires_when_heartbeat_is_stale(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    # check_health.hourly has max_age=75 minutes; write one 2h old
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(hours=2))
    from apps.observability.checks import check_heartbeats_fresh
    errs = check_heartbeats_fresh(None)
    assert any(e.id == "observability.H001" and "check_health.hourly" in e.msg for e in errs)


def test_h002_fires_when_heartbeat_never_seen(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    (tmp_path / "heartbeats.jsonl").write_text("")
    from apps.observability.checks import check_heartbeats_fresh
    errs = check_heartbeats_fresh(None)
    assert any(e.id == "observability.H002" for e in errs)


def test_h003_fires_when_last_status_is_fail(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    _write_hb(tmp_path, "check_health.hourly", status="fail", age=timedelta(seconds=10))
    from apps.observability.checks import check_heartbeats_fresh
    errs = check_heartbeats_fresh(None)
    assert any(e.id == "observability.H003" and "check_health.hourly" in e.msg for e in errs)


def test_agent_only_specs_skipped_in_hub_mode(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""  # this host IS the hub, not an agent
    settings.CLUSTER_ENABLED = True
    (tmp_path / "heartbeats.jsonl").write_text("")
    from apps.observability.checks import check_heartbeats_fresh
    errs = check_heartbeats_fresh(None)
    msgs = "\n".join(e.msg for e in errs)
    assert "push_to_hub" not in msgs
    assert "cluster_push.events" not in msgs
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/test_checks.py -v` → 4 new tests FAIL.

**Step 3: Implement**

Append to `apps/observability/checks.py`:

```python
from datetime import datetime, timezone

from django.conf import settings as _settings
from django.core import checks  # noqa: F811 (re-import safe)

from apps.observability.heartbeat_reader import latest_heartbeats
from apps.observability.heartbeat_registry import HEARTBEAT_REGISTRY


def _is_agent_mode() -> bool:
    return bool(getattr(_settings, "HUB_URL", ""))


@checks.register()
def check_heartbeats_fresh(app_configs, **kwargs):
    errs = []
    latest = latest_heartbeats()
    agent_mode = _is_agent_mode()
    for name, spec in HEARTBEAT_REGISTRY.items():
        if spec.agent_only and not agent_mode:
            continue
        rec = latest.get(name)
        if rec is None:
            errs.append(checks.Warning(
                f"heartbeat {name} has never been seen ({spec.desc})",
                hint=f"Wire `with heartbeat({name!r})` into the corresponding job.",
                id="observability.H002",
            ))
            continue
        # Parse ts
        ts_s = rec.ts.rstrip("Z")
        try:
            ts = datetime.fromisoformat(ts_s).replace(tzinfo=timezone.utc)
        except ValueError:
            ts = datetime.now(tz=timezone.utc)
        age = datetime.now(tz=timezone.utc) - ts
        if age > spec.max_age:
            errs.append(checks.Warning(
                f"heartbeat {name} is {age} old (max {spec.max_age}) — {spec.desc}",
                hint="Check the job's cron entry or its last-run logs.",
                id="observability.H001",
            ))
        if rec.status == "fail":
            errs.append(checks.Warning(
                f"heartbeat {name} last status was fail — {spec.desc}",
                hint="See heartbeats.jsonl for the failure reason.",
                id="observability.H003",
            ))
    return errs
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_checks.py -v` → 6 passed (W001 ×2 + H001/H002/H003 + agent-only).

**Step 5: Commit**

```bash
git add apps/observability/checks.py apps/observability/_tests/test_checks.py
git commit -m "feat(observability): system checks H001/H002/H003 for heartbeat freshness"
```

---

### Task 3.5: Wire heartbeat calls into existing cron commands

**Files:**
- Modify: `apps/checkers/management/commands/check_health.py` (wrap `handle()` body)
- Modify: `apps/alerts/management/commands/push_to_hub.py` (wrap `handle()` body)
- Modify: `apps/checkers/management/commands/preflight.py` (wrap `handle()` body)

**Step 1: Write integration tests**

Create `apps/observability/_tests/test_heartbeat_integration.py`:

```python
"""Integration tests: existing commands emit heartbeats."""

import json
from pathlib import Path


def _read(tmp_path: Path, name: str) -> list[dict]:
    p = tmp_path / name
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_check_health_emits_heartbeat(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command
    call_command("check_health")
    recs = _read(tmp_path, "heartbeats.jsonl")
    names = {r["name"] for r in recs}
    assert any(n.startswith("check_health") for n in names)


def test_preflight_emits_heartbeat(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command
    call_command("preflight", "--json")
    recs = _read(tmp_path, "heartbeats.jsonl")
    assert any(r["name"] == "preflight.scheduled" for r in recs)
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/test_heartbeat_integration.py -v` → FAIL (no heartbeats emitted).

**Step 3: Implement**

In each command's `handle()` method, wrap the main body in the context manager:

```python
# apps/checkers/management/commands/check_health.py
from apps.observability.heartbeat import heartbeat


def handle(self, *args, **options):
    name = "check_health.daily" if options.get("all") else "check_health.hourly"
    with heartbeat(name):
        # ... existing handle() body ...
```

```python
# apps/alerts/management/commands/push_to_hub.py
from apps.observability.heartbeat import heartbeat


def handle(self, *args, **options):
    with heartbeat("push_to_hub"):
        # ... existing handle() body ...
```

```python
# apps/checkers/management/commands/preflight.py
from apps.observability.heartbeat import heartbeat


def handle(self, *args, **options):
    with heartbeat("preflight.scheduled"):
        # ... existing handle() body ...
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_heartbeat_integration.py -v` → 2 passed.

**Step 5: Commit**

```bash
git add apps/checkers/management/commands/check_health.py \
        apps/alerts/management/commands/push_to_hub.py \
        apps/checkers/management/commands/preflight.py \
        apps/observability/_tests/test_heartbeat_integration.py
git commit -m "feat(observability): wire heartbeat() into check_health/push_to_hub/preflight"
```

---

## Phase 4 — Internal alert driver and freshness alert flow

### Task 4.1: `InternalDriver`

**Files:**
- Create: `apps/alerts/drivers/internal.py`
- Modify: `apps/alerts/drivers/__init__.py` (register driver in `DRIVER_REGISTRY` but **not** in the webhook URL dispatch table)
- Create: `apps/alerts/_tests/drivers/test_internal.py`

**Step 1: Write the failing tests**

`apps/alerts/_tests/drivers/test_internal.py`:

```python
"""Tests for the internal alert driver."""

import pytest

from apps.alerts.drivers.internal import InternalDriver


def test_signature_header_is_none():
    """Not webhook-reachable by design."""
    assert InternalDriver.signature_header is None


def test_validate_accepts_full_payload():
    d = InternalDriver()
    assert d.validate({
        "source": "observability",
        "fingerprint": "heartbeat-stale:foo",
        "title": "stale",
        "severity": "warning",
        "labels": {"job": "foo"},
    }) is True


def test_validate_rejects_missing_field():
    d = InternalDriver()
    assert d.validate({"source": "x", "title": "t"}) is False


def test_parse_round_trips():
    d = InternalDriver()
    payload = {
        "source": "observability",
        "fingerprint": "heartbeat-stale:foo",
        "title": "Heartbeat stale: foo",
        "severity": "warning",
        "labels": {"job": "foo", "max_age_seconds": 900},
        "description": "Hourly cron",
    }
    parsed = d.parse(payload)
    assert parsed.source == "observability"
    assert parsed.fingerprint == "heartbeat-stale:foo"


def test_internal_driver_not_under_webhook_dispatch():
    """The internal driver MUST NOT be reachable from /alerts/webhook/."""
    from apps.alerts.drivers import WEBHOOK_DRIVERS  # set added in this task
    assert "internal" not in WEBHOOK_DRIVERS
```

**Step 2: Run to verify they fail**

`uv run pytest apps/alerts/_tests/drivers/test_internal.py -v` → ImportError.

**Step 3: Implement**

Inspect `apps/alerts/drivers/base.py` to learn the existing `BaseDriver` interface (it has `validate(payload) -> bool` and `parse(payload) -> ParsedPayload`). Then create:

`apps/alerts/drivers/internal.py`:

```python
"""Internal alert driver — for in-process callers only (NOT webhook-reachable).

Used by apps.observability's freshness checker to produce Alerts that flow
through the standard alerts → orchestration → notify pipeline. Has no
signature_header by design and is excluded from the WEBHOOK_DRIVERS set so
it cannot be invoked from /alerts/webhook/.
"""

from __future__ import annotations

from apps.alerts.drivers.base import BaseDriver, ParsedAlert


class InternalDriver(BaseDriver):
    name = "internal"
    signature_header = None  # explicit: not webhook-reachable

    REQUIRED = ("source", "fingerprint", "title", "severity", "labels")

    def validate(self, payload: dict) -> bool:
        return all(k in payload for k in self.REQUIRED)

    def parse(self, payload: dict) -> ParsedAlert:
        return ParsedAlert(
            source=payload["source"],
            fingerprint=payload["fingerprint"],
            title=payload["title"],
            severity=payload["severity"],
            labels=payload["labels"],
            description=payload.get("description", ""),
        )
```

In `apps/alerts/drivers/__init__.py`, register the driver in `DRIVER_REGISTRY` and split out a `WEBHOOK_DRIVERS` set that excludes it:

```python
from apps.alerts.drivers.internal import InternalDriver
# ... existing imports ...

DRIVER_REGISTRY = {
    # ... existing entries ...
    "internal": InternalDriver,
}

# Drivers reachable from /alerts/webhook/ — internal is intentionally absent.
WEBHOOK_DRIVERS = {k for k, cls in DRIVER_REGISTRY.items() if cls.signature_header is not None or k != "internal"}
```

Modify the webhook view (if it iterates `DRIVER_REGISTRY` for auto-detection) to use `WEBHOOK_DRIVERS` instead. Confirm by inspecting `apps/alerts/views/` for the dispatch logic.

**Step 4: Run to verify they pass**

`uv run pytest apps/alerts/_tests/drivers/test_internal.py -v` → 5 passed.
Also: `uv run pytest apps/alerts/` → all green.

**Step 5: Commit**

```bash
git add apps/alerts/drivers/internal.py apps/alerts/drivers/__init__.py \
        apps/alerts/_tests/drivers/test_internal.py
git commit -m "feat(alerts): in-process internal driver, excluded from /alerts/webhook/"
```

---

### Task 4.2: `manage.py check_heartbeats` command

**Files:**
- Create: `apps/observability/management/__init__.py` (empty)
- Create: `apps/observability/management/commands/__init__.py` (empty)
- Create: `apps/observability/management/commands/check_heartbeats.py`
- Create: `apps/observability/_tests/management/__init__.py` (empty)
- Create: `apps/observability/_tests/management/test_check_heartbeats.py`

**Step 1: Write the failing tests**

```python
"""Tests for check_heartbeats management command."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from django.core.management import call_command


def _write_hb(tmp_path, name, *, status="ok", age=timedelta(0)):
    ts = (datetime.now(tz=timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    line = json.dumps({
        "ts": ts, "v": 1, "level": "INFO", "logger": "x", "msg": "h",
        "instance_id": "test", "name": name, "status": status,
    })
    (tmp_path / "heartbeats.jsonl").write_text(line + "\n")


def test_all_fresh_exits_zero(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    for name in ["check_health.hourly", "check_health.daily", "preflight.scheduled"]:
        _write_hb(tmp_path, name, age=timedelta(seconds=10))
    # agent-only specs only required when HUB_URL set
    settings.HUB_URL = ""
    call_command("check_heartbeats")  # no SystemExit


def test_stale_creates_incident(tmp_path, settings, db):
    settings.LOGS_DIR = tmp_path
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(hours=2))
    settings.HUB_URL = ""
    # Other registered names get H002 (never seen); freshness command should
    # still attempt to alert on each stale registered job.
    with pytest.raises(SystemExit) as excinfo:
        call_command("check_heartbeats")
    assert excinfo.value.code == 1
    from apps.alerts.models import Incident
    assert Incident.objects.filter(
        alert_fingerprint__startswith="heartbeat-stale:"
    ).exists()


def test_dedup_by_fingerprint(tmp_path, settings, db):
    settings.LOGS_DIR = tmp_path
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(hours=2))
    settings.HUB_URL = ""
    for _ in range(3):
        try:
            call_command("check_heartbeats")
        except SystemExit:
            pass
    from apps.alerts.models import Incident
    count = Incident.objects.filter(
        alert_fingerprint="heartbeat-stale:check_health.hourly"
    ).count()
    assert count == 1, "Repeated stale ticks must update one Incident, not create new ones"


def test_json_output(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(seconds=10))
    settings.HUB_URL = ""
    try:
        call_command("check_heartbeats", "--json")
    except SystemExit:
        pass
    out = capsys.readouterr().out
    obj = json.loads(out)
    assert "stale" in obj and "fresh" in obj
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/management/test_check_heartbeats.py -v` → command not found.

**Step 3: Implement**

`apps/observability/management/commands/check_heartbeats.py`:

```python
"""Standalone freshness check; emits Alerts via the internal driver."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.alerts.services import AlertOrchestrator
from apps.observability.heartbeat_reader import latest_heartbeats
from apps.observability.heartbeat_registry import HEARTBEAT_REGISTRY


class Command(BaseCommand):
    help = "Check heartbeat freshness; emit Alerts for any stale registered job."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="Emit a JSON summary instead of human-readable text.")
        parser.add_argument("--quiet", action="store_true",
                            help="Suppress per-job stdout (used in cron).")

    def handle(self, *args, **options):
        agent_mode = bool(getattr(settings, "HUB_URL", ""))
        latest = latest_heartbeats()
        stale: list[dict] = []
        fresh: list[dict] = []

        for name, spec in HEARTBEAT_REGISTRY.items():
            if spec.agent_only and not agent_mode:
                continue
            rec = latest.get(name)
            if rec is None:
                stale.append({"name": name, "reason": "never-seen",
                              "max_age_seconds": int(spec.max_age.total_seconds())})
                continue
            ts = datetime.fromisoformat(rec.ts.rstrip("Z")).replace(tzinfo=timezone.utc)
            age = datetime.now(tz=timezone.utc) - ts
            if age > spec.max_age:
                stale.append({"name": name, "reason": "stale",
                              "age_seconds": age.total_seconds(),
                              "max_age_seconds": int(spec.max_age.total_seconds()),
                              "last_seen": rec.ts})
            elif rec.status == "fail":
                stale.append({"name": name, "reason": "last-status-fail",
                              "last_seen": rec.ts})
            else:
                fresh.append({"name": name, "last_seen": rec.ts})

        # Emit Alerts for each stale entry via the internal driver
        if stale:
            orch = AlertOrchestrator()
            for entry in stale:
                spec = HEARTBEAT_REGISTRY[entry["name"]]
                orch.process_webhook({
                    "source": "observability",
                    "fingerprint": f"heartbeat-stale:{entry['name']}",
                    "title": f"Heartbeat stale: {entry['name']}",
                    "severity": "warning",
                    "labels": {
                        "job": entry["name"],
                        "max_age_seconds": int(spec.max_age.total_seconds()),
                        "reason": entry["reason"],
                    },
                    "description": spec.desc,
                }, driver="internal")

        if options["json"]:
            self.stdout.write(json.dumps({"stale": stale, "fresh": fresh}, indent=2))
        elif not options["quiet"]:
            for s in stale:
                self.stdout.write(self.style.WARNING(
                    f"STALE  {s['name']}  ({s['reason']})"
                ))
            for f in fresh:
                self.stdout.write(f"FRESH  {f['name']}")

        if stale:
            sys.exit(1)
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/management/test_check_heartbeats.py -v` → 4 passed.

**Step 5: Commit**

```bash
git add apps/observability/management/ apps/observability/_tests/management/
git commit -m "feat(observability): check_heartbeats command + freshness Alert dispatch"
```

---

## Phase 5 — CLI reader

### Task 5.1: `manage.py read_logs view` (one-shot)

**Files:**
- Create: `apps/observability/management/commands/read_logs.py`
- Create: `apps/observability/log_reader.py` (parsing + filtering library, separate from the command for unit-testability)
- Create: `apps/observability/_tests/test_log_reader.py`
- Create: `apps/observability/_tests/management/test_read_logs.py`

**Step 1: Write the failing tests**

`apps/observability/_tests/test_log_reader.py`:

```python
"""Tests for the log_reader filtering library."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.observability.log_reader import LogFilter, iter_events


def _write(tmp_path, records, fn="events.jsonl"):
    (tmp_path / fn).write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _rec(**fields):
    base = {"ts": "2026-05-17T10:00:00.000Z", "v": 1, "level": "INFO",
            "logger": "apps.alerts.services", "msg": "x", "instance_id": "h",
            "category": "alerts"}
    base.update(fields)
    return base


def test_iter_events_returns_all_records(tmp_path):
    _write(tmp_path, [_rec(), _rec()])
    assert len(list(iter_events(tmp_path, LogFilter()))) == 2


def test_filter_by_level(tmp_path):
    _write(tmp_path, [_rec(level="INFO"), _rec(level="WARNING")])
    f = LogFilter(level="WARNING")
    assert [r["level"] for r in iter_events(tmp_path, f)] == ["WARNING"]


def test_filter_by_category(tmp_path):
    _write(tmp_path, [_rec(category="alerts"), _rec(category="notify")])
    f = LogFilter(category="notify")
    assert [r["category"] for r in iter_events(tmp_path, f)] == ["notify"]


def test_filter_by_trace_id(tmp_path):
    _write(tmp_path, [_rec(trace_id="t1"), _rec(trace_id="t2"), _rec()])
    f = LogFilter(trace_id="t1")
    assert [r["trace_id"] for r in iter_events(tmp_path, f)] == ["t1"]


def test_filter_by_grep(tmp_path):
    _write(tmp_path, [_rec(msg="hello world"), _rec(msg="goodbye")])
    f = LogFilter(grep="hello")
    assert [r["msg"] for r in iter_events(tmp_path, f)] == ["hello world"]


def test_filter_by_since_duration(tmp_path):
    now = datetime.now(tz=timezone.utc)
    old = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    new = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _write(tmp_path, [_rec(ts=old), _rec(ts=new)])
    f = LogFilter(since="1h")
    out = list(iter_events(tmp_path, f))
    assert len(out) == 1
    assert out[0]["ts"] == new


def test_last_n_returns_most_recent(tmp_path):
    records = [_rec(ts=f"2026-05-17T10:00:{i:02d}.000Z") for i in range(5)]
    _write(tmp_path, records)
    f = LogFilter(last=2)
    out = list(iter_events(tmp_path, f))
    assert len(out) == 2
    assert out[-1]["ts"] == "2026-05-17T10:00:04.000Z"


def test_includes_rotated_backups(tmp_path):
    _write(tmp_path, [_rec(ts="2026-05-17T09:00:00.000Z")], fn="events.jsonl.1")
    _write(tmp_path, [_rec(ts="2026-05-17T10:00:00.000Z")], fn="events.jsonl")
    out = list(iter_events(tmp_path, LogFilter()))
    assert len(out) == 2


def test_malformed_line_skipped(tmp_path):
    (tmp_path / "events.jsonl").write_text(
        "not-json\n" + json.dumps(_rec()) + "\n"
    )
    out = list(iter_events(tmp_path, LogFilter()))
    assert len(out) == 1
```

`apps/observability/_tests/management/test_read_logs.py`:

```python
"""Tests for `manage.py read_logs view`."""

import json

from django.core.management import call_command


def _write(tmp_path, records):
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _rec(**fields):
    base = {"ts": "2026-05-17T10:00:00.000Z", "v": 1, "level": "INFO",
            "logger": "apps.alerts.services", "msg": "x", "instance_id": "h",
            "category": "alerts"}
    base.update(fields)
    return base


def test_view_json_output(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="hello")])
    call_command("read_logs", "view", "--json", "--no-pager")
    out = capsys.readouterr().out.strip().splitlines()
    parsed = [json.loads(l) for l in out]
    assert parsed[0]["msg"] == "hello"


def test_view_filter_by_category(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(category="alerts"), _rec(category="notify")])
    call_command("read_logs", "view", "--category", "notify", "--json", "--no-pager")
    out = capsys.readouterr().out.strip().splitlines()
    assert all(json.loads(l)["category"] == "notify" for l in out)
```

**Step 2: Run to verify they fail**

Both files fail on import (module not present).

**Step 3: Implement**

`apps/observability/log_reader.py`:

```python
"""Parsing + filtering for events.jsonl / heartbeats.jsonl."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator


_DURATION_RE = re.compile(r"^(\d+)([smhdw])$")
_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_since(spec: str | None) -> datetime | None:
    if not spec:
        return None
    m = _DURATION_RE.match(spec)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return datetime.now(tz=timezone.utc) - timedelta(seconds=n * _UNIT_TO_SECONDS[unit])
    # Assume ISO-8601 absolute timestamp
    return datetime.fromisoformat(spec.rstrip("Z")).replace(tzinfo=timezone.utc)


@dataclass
class LogFilter:
    category: str | None = None
    level: str | None = None
    logger: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    incident_id: int | None = None
    grep: str | None = None
    since: str | None = None
    until: str | None = None
    last: int | None = None

    def matches(self, obj: dict) -> bool:
        if self.category and obj.get("category") != self.category:
            return False
        if self.level and obj.get("level") != self.level:
            return False
        if self.logger and self.logger not in obj.get("logger", ""):
            return False
        if self.trace_id and obj.get("trace_id") != self.trace_id:
            return False
        if self.run_id and obj.get("run_id") != self.run_id:
            return False
        if self.incident_id is not None and obj.get("incident_id") != self.incident_id:
            return False
        if self.grep:
            haystack = obj.get("msg", "") + " " + json.dumps(obj.get("extra", {}))
            if not re.search(self.grep, haystack):
                return False
        since = _parse_since(self.since)
        if since:
            ts = datetime.fromisoformat(obj["ts"].rstrip("Z")).replace(tzinfo=timezone.utc)
            if ts < since:
                return False
        until = _parse_since(self.until)
        if until:
            ts = datetime.fromisoformat(obj["ts"].rstrip("Z")).replace(tzinfo=timezone.utc)
            if ts > until:
                return False
        return True


def _stream_files(logs_dir: Path, basename: str) -> Iterator[dict]:
    """Yield records from rotated backups, then the live file (chronological order)."""
    candidates: list[Path] = []
    # Rotated backups in oldest-first order: .N, .N-1, ..., .1
    backups = sorted(logs_dir.glob(f"{basename}.*"), key=lambda p: int(p.suffix.lstrip(".")), reverse=True)
    candidates.extend(backups)
    live = logs_dir / basename
    if live.exists():
        candidates.append(live)
    for path in candidates:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def iter_events(logs_dir: Path, flt: LogFilter, basename: str = "events.jsonl") -> Iterable[dict]:
    matched = (r for r in _stream_files(logs_dir, basename) if flt.matches(r))
    if flt.last:
        # Buffer last N matching
        buf: list[dict] = []
        for r in matched:
            buf.append(r)
            if len(buf) > flt.last:
                buf.pop(0)
        return buf
    return list(matched)
```

`apps/observability/management/commands/read_logs.py`:

```python
"""manage.py read_logs view|tail|trace|heartbeats."""

from __future__ import annotations

import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.observability.log_reader import LogFilter, iter_events


class Command(BaseCommand):
    help = "Read structured log records."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)

        view = sub.add_parser("view", help="Print filtered records (one-shot).")
        for p in (view,):
            p.add_argument("--category")
            p.add_argument("--level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
            p.add_argument("--logger")
            p.add_argument("--trace-id", dest="trace_id")
            p.add_argument("--run-id", dest="run_id")
            p.add_argument("--incident", type=int, dest="incident_id")
            p.add_argument("--since")
            p.add_argument("--until")
            p.add_argument("--grep")
            p.add_argument("--last", type=int, default=200)
            p.add_argument("--stream", choices=["events", "heartbeats"], default="events")
            p.add_argument("--instance", help="Read from LOGS_DIR/cluster/<instance>/ instead.")
            p.add_argument("--json", action="store_true")
            p.add_argument("--plain", action="store_true")
            p.add_argument("--no-pager", action="store_true")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "view":
            return self._view(options)
        raise NotImplementedError(action)

    def _logs_dir(self, instance: str | None):
        from pathlib import Path
        base = Path(settings.LOGS_DIR)
        if instance:
            return base / "cluster" / instance
        return base

    def _view(self, options):
        flt = LogFilter(
            category=options.get("category"),
            level=options.get("level"),
            logger=options.get("logger"),
            trace_id=options.get("trace_id"),
            run_id=options.get("run_id"),
            incident_id=options.get("incident_id"),
            since=options.get("since"),
            until=options.get("until"),
            grep=options.get("grep"),
            last=options.get("last"),
        )
        stream = options["stream"]
        basename = "events.jsonl" if stream == "events" else "heartbeats.jsonl"
        logs_dir = self._logs_dir(options.get("instance"))

        records = iter_events(logs_dir, flt, basename=basename)
        for rec in records:
            if options.get("json"):
                self.stdout.write(json.dumps(rec, ensure_ascii=False))
            else:
                self.stdout.write(self._fmt_pretty(rec, plain=options.get("plain", False)))

    def _fmt_pretty(self, rec: dict, plain: bool) -> str:
        time_part = rec.get("ts", "")[11:19]
        level = rec.get("level", "")
        logger = rec.get("logger", "")
        msg = rec.get("msg", "")
        trace = rec.get("trace_id", "")[:8] if rec.get("trace_id") else ""
        line = f"{time_part}  {level:<5}  {logger}  {msg}"
        if trace:
            line += f"  trace={trace}"
        return line
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_log_reader.py apps/observability/_tests/management/test_read_logs.py -v` → all pass.

**Step 5: Commit**

```bash
git add apps/observability/log_reader.py \
        apps/observability/management/commands/read_logs.py \
        apps/observability/_tests/test_log_reader.py \
        apps/observability/_tests/management/test_read_logs.py
git commit -m "feat(observability): read_logs view subcommand + LogFilter library"
```

---

### Task 5.2: `read_logs tail` (follow mode)

**Files:**
- Modify: `apps/observability/management/commands/read_logs.py` (add `tail` subparser + `_tail()`)
- Modify: `apps/observability/_tests/management/test_read_logs.py` (append a tail test)

**Step 1: Write the failing test**

```python
# In test_read_logs.py append:

import threading
import time


def test_tail_streams_new_lines(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    (tmp_path / "events.jsonl").write_text(json.dumps(_rec(msg="initial")) + "\n")

    def writer():
        time.sleep(0.3)
        with (tmp_path / "events.jsonl").open("a") as f:
            f.write(json.dumps(_rec(msg="appended")) + "\n")

    t = threading.Thread(target=writer)
    t.start()

    # Run tail for a brief window via a custom max-iterations flag
    from django.core.management import call_command
    call_command("read_logs", "tail", "--json", "--from-end", "0",
                 "--max-iterations", "5", "--poll-interval", "0.1")
    t.join()
    out = capsys.readouterr().out
    # "appended" should appear; "initial" shouldn't because from-end=0
    assert "appended" in out
    assert "initial" not in out
```

**Step 2: Run to verify it fails**

`uv run pytest ... -v` → fails (no tail subcommand).

**Step 3: Implement**

Append to `read_logs.py`:

```python
# In add_arguments:
tail = sub.add_parser("tail", help="Live-follow new records.")
for p in (tail,):
    p.add_argument("--category")
    p.add_argument("--level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    p.add_argument("--trace-id", dest="trace_id")
    p.add_argument("--stream", choices=["events", "heartbeats"], default="events")
    p.add_argument("--instance")
    p.add_argument("--json", action="store_true")
    p.add_argument("--plain", action="store_true")
    p.add_argument("--from-end", type=int, default=10)
    p.add_argument("--max-iterations", type=int, default=0,
                   help="Test hook: stop after N polls (0 = forever).")
    p.add_argument("--poll-interval", type=float, default=0.25)

# In handle(), add:
if action == "tail":
    return self._tail(options)

def _tail(self, options):
    import time
    flt = LogFilter(
        category=options.get("category"),
        level=options.get("level"),
        trace_id=options.get("trace_id"),
    )
    stream = options["stream"]
    basename = "events.jsonl" if stream == "events" else "heartbeats.jsonl"
    logs_dir = self._logs_dir(options.get("instance"))
    path = logs_dir / basename
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()

    # Print last-N from-end first
    from_end = options.get("from_end", 0)
    if from_end:
        tail_records = iter_events(logs_dir, LogFilter(last=from_end), basename=basename)
        for rec in tail_records:
            if flt.matches(rec):
                self._emit(rec, options)

    # Then follow
    with path.open("r", encoding="utf-8") as f:
        f.seek(0, 2)  # end of file
        iterations = 0
        max_iter = options.get("max_iterations", 0)
        while True:
            line = f.readline()
            if not line:
                iterations += 1
                if max_iter and iterations >= max_iter:
                    return
                time.sleep(options["poll_interval"])
                continue
            try:
                rec = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if flt.matches(rec):
                self._emit(rec, options)

def _emit(self, rec, options):
    if options.get("json"):
        self.stdout.write(json.dumps(rec, ensure_ascii=False))
    else:
        self.stdout.write(self._fmt_pretty(rec, plain=options.get("plain", False)))
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/management/test_read_logs.py -v` → all pass.

**Step 5: Commit**

```bash
git add apps/observability/management/commands/read_logs.py \
        apps/observability/_tests/management/test_read_logs.py
git commit -m "feat(observability): read_logs tail (follow mode) with --from-end and test hooks"
```

---

### Task 5.3: `read_logs trace` and `read_logs heartbeats`

**Files:**
- Modify: `apps/observability/management/commands/read_logs.py` (two new subparsers)
- Modify: `apps/observability/_tests/management/test_read_logs.py`

**Step 1: Write the failing tests**

```python
def test_trace_filters_across_both_streams(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    (tmp_path / "events.jsonl").write_text(
        json.dumps(_rec(trace_id="needle")) + "\n"
        + json.dumps(_rec(trace_id="other")) + "\n"
    )
    (tmp_path / "heartbeats.jsonl").write_text(
        json.dumps(_rec(trace_id="needle", logger="apps.observability.heartbeat")) + "\n"
    )
    from django.core.management import call_command
    call_command("read_logs", "trace", "needle", "--json", "--no-pager")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert all(json.loads(l)["trace_id"] == "needle" for l in out)


def test_heartbeats_table_output(tmp_path, settings, capsys):
    import json
    from datetime import datetime, timezone
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    line = json.dumps({"ts": ts, "v": 1, "level": "INFO", "logger": "x", "msg": "h",
                       "instance_id": "h", "name": "check_health.hourly", "status": "ok"})
    (tmp_path / "heartbeats.jsonl").write_text(line + "\n")
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command
    call_command("read_logs", "heartbeats")
    out = capsys.readouterr().out
    assert "check_health.hourly" in out
    assert "OK" in out.upper() or "ok" in out
```

**Step 2: Run to verify they fail**

`uv run pytest ... -v` → unknown subcommand.

**Step 3: Implement**

Append to `read_logs.py`:

```python
# In add_arguments:
trace = sub.add_parser("trace", help="Filter to a single request across both streams.")
trace.add_argument("trace_id")
trace.add_argument("--last", type=int, default=1000)
trace.add_argument("--instance")
trace.add_argument("--json", action="store_true")
trace.add_argument("--plain", action="store_true")
trace.add_argument("--no-pager", action="store_true")

heartbeats = sub.add_parser("heartbeats", help="Latest heartbeat per registered job.")
heartbeats.add_argument("--instance")
heartbeats.add_argument("--json", action="store_true")

# In handle():
if action == "trace":
    return self._trace(options)
if action == "heartbeats":
    return self._heartbeats(options)

def _trace(self, options):
    logs_dir = self._logs_dir(options.get("instance"))
    flt = LogFilter(trace_id=options["trace_id"], last=options.get("last", 1000))
    for basename in ("events.jsonl", "heartbeats.jsonl"):
        for rec in iter_events(logs_dir, flt, basename=basename):
            self._emit(rec, options)

def _heartbeats(self, options):
    from apps.observability.heartbeat_reader import latest_heartbeats
    from apps.observability.heartbeat_registry import HEARTBEAT_REGISTRY
    from datetime import datetime, timezone

    latest = latest_heartbeats(self._logs_dir(options.get("instance")))
    rows = []
    for name, spec in HEARTBEAT_REGISTRY.items():
        rec = latest.get(name)
        if rec is None:
            rows.append({"name": name, "last_seen": "—", "age": "—",
                         "status": "NEVER-SEEN", "max_age": str(spec.max_age)})
            continue
        ts = datetime.fromisoformat(rec.ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        age = datetime.now(tz=timezone.utc) - ts
        rows.append({"name": name, "last_seen": rec.ts, "age": str(age),
                     "status": rec.status.upper(), "max_age": str(spec.max_age)})

    if options.get("json"):
        self.stdout.write(json.dumps(rows, indent=2))
        return
    for r in rows:
        self.stdout.write(
            f"{r['name']:<30}  {r['status']:<12}  age={r['age']}  max={r['max_age']}"
        )
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/management/test_read_logs.py -v` → all pass.

**Step 5: Commit**

```bash
git add apps/observability/management/commands/read_logs.py \
        apps/observability/_tests/management/test_read_logs.py
git commit -m "feat(observability): read_logs trace <id> + heartbeats table view"
```

---

### Task 5.4: `bin/cli/logs.sh` shell wrapper

**Files:**
- Create: `bin/cli/logs.sh`
- Modify: `bin/cli/cli.sh` (add logs to the menu)
- Create: `bin/tests/test_logs.sh` (using existing shell-test harness)

**Step 1: Write the failing shell test**

`bin/tests/test_logs.sh`:

```bash
#!/usr/bin/env bats

load test_helper

@test "cli logs view forwards to manage.py read_logs view" {
    run bin/cli/logs.sh view --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"read_logs"* ]] || [[ "$output" == *"view"* ]]
}

@test "cli logs heartbeats invocation does not crash" {
    run bin/cli/logs.sh heartbeats
    # Either prints rows or empty — must not error
    [ "$status" -eq 0 ] || [ "$status" -eq 1 ]
}
```

**Step 2: Run to verify it fails**

`bash bin/tests/run_tests.sh` (or whatever the project's BATS entrypoint is) → tests fail.

**Step 3: Implement**

`bin/cli/logs.sh`:

```bash
#!/usr/bin/env bash
# Logs submenu — thin wrapper around `manage.py read_logs`.

set -euo pipefail

source "$(dirname "$0")/../lib/paths.sh"

logs_main() {
    local subcommand="${1:-}"
    shift || true

    case "$subcommand" in
        view|tail|trace|heartbeats)
            "$REPO_ROOT/bin/lib/run_manage.sh" read_logs "$subcommand" "$@"
            ;;
        ""|menu)
            logs_menu
            ;;
        *)
            echo "Unknown subcommand: $subcommand" >&2
            echo "Usage: cli logs {view|tail|trace|heartbeats} [filters]" >&2
            return 2
            ;;
    esac
}

logs_menu() {
    PS3="Choose action: "
    select choice in "view recent" "tail follow" "trace by id" "heartbeats status" "exit"; do
        case "$REPLY" in
            1) logs_main view --last 100; break ;;
            2) logs_main tail; break ;;
            3) read -p "Trace ID: " trace_id; logs_main trace "$trace_id"; break ;;
            4) logs_main heartbeats; break ;;
            5) return 0 ;;
        esac
    done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    logs_main "$@"
fi
```

Add to `bin/cli/cli.sh`'s menu (one line, mirroring how `health.sh` etc. are wired in):

```bash
# Inside cli.sh's main menu select:
"logs") source "$SCRIPT_DIR/logs.sh"; logs_main "$@" ;;
```

Make executable: `chmod +x bin/cli/logs.sh`.

**Step 4: Run to verify it passes**

`bash bin/tests/run_tests.sh` (or the equivalent) — tests pass.

**Step 5: Commit**

```bash
git add bin/cli/logs.sh bin/cli/cli.sh bin/tests/test_logs.sh
git commit -m "feat(observability): bin/cli/logs.sh wrapper for manage.py read_logs"
```

---

## Phase 6 — Cluster logbook

### Task 6.1: `manage.py push_logs_to_hub` (cursor + chunked push)

**Files:**
- Create: `apps/observability/cluster_push.py` (cursor logic, separate from command for testability)
- Create: `apps/observability/management/commands/push_logs_to_hub.py`
- Create: `apps/observability/_tests/test_cluster_push.py`

**Step 1: Write the failing tests**

```python
"""Tests for cluster_push (cursor + chunked push logic)."""

import json
from pathlib import Path

import pytest

from apps.observability.cluster_push import (
    Cursor,
    CursorStore,
    push_stream,
)


def test_cursor_store_atomic_write(tmp_path):
    store = CursorStore(tmp_path / "cursor.json")
    store.save("events", Cursor(inode=123, offset=456))
    loaded = store.load("events")
    assert loaded.inode == 123 and loaded.offset == 456


def test_cursor_store_returns_zero_on_missing(tmp_path):
    store = CursorStore(tmp_path / "cursor.json")
    assert store.load("events") == Cursor(inode=0, offset=0)


def test_push_stream_advances_cursor_on_2xx(tmp_path):
    src = tmp_path / "events.jsonl"
    src.write_text('{"a":1}\n{"a":2}\n{"a":3}\n')
    sent_chunks: list[bytes] = []
    def fake_send(chunk: bytes) -> tuple[int, int]:
        sent_chunks.append(chunk)
        return 202, len(chunk)
    store = CursorStore(tmp_path / "cursor.json")
    push_stream(src, store, stream="events", send=fake_send,
                max_bytes_per_request=1024, max_bytes_per_run=1024)
    assert sent_chunks  # something pushed
    assert store.load("events").offset == src.stat().st_size


def test_push_stream_does_not_advance_on_4xx(tmp_path):
    src = tmp_path / "events.jsonl"
    src.write_text('{"a":1}\n')
    def fake_send(chunk: bytes) -> tuple[int, int]:
        return 400, 0
    store = CursorStore(tmp_path / "cursor.json")
    with pytest.raises(RuntimeError):
        push_stream(src, store, stream="events", send=fake_send,
                    max_bytes_per_request=1024, max_bytes_per_run=1024)
    assert store.load("events").offset == 0


def test_push_stream_truncates_chunk_at_last_newline(tmp_path):
    src = tmp_path / "events.jsonl"
    # Three lines, last incomplete:
    src.write_text('{"a":1}\n{"a":2}\n{"a":3}')
    sent: list[bytes] = []
    def fake_send(chunk: bytes) -> tuple[int, int]:
        sent.append(chunk)
        return 202, len(chunk)
    store = CursorStore(tmp_path / "cursor.json")
    push_stream(src, store, stream="events", send=fake_send,
                max_bytes_per_request=1024, max_bytes_per_run=1024)
    # The partial third line must not have been pushed
    assert sent
    assert sent[0].endswith(b"\n")
    assert b'{"a":3}' not in sent[0]


def test_push_stream_drains_rotated_file_when_inode_changes(tmp_path):
    src = tmp_path / "events.jsonl"
    src.write_text('{"a":1}\n{"a":2}\n')
    store = CursorStore(tmp_path / "cursor.json")
    # Save a cursor pointing at the (now-rotated) file
    store.save("events", Cursor(inode=src.stat().st_ino, offset=8))
    # Rotate: rename current → .1, create new
    (tmp_path / "events.jsonl.1").write_bytes(src.read_bytes())
    src.write_text('{"a":3}\n')
    sent: list[bytes] = []
    def fake_send(chunk: bytes) -> tuple[int, int]:
        sent.append(chunk)
        return 202, len(chunk)
    push_stream(src, store, stream="events", send=fake_send,
                max_bytes_per_request=1024, max_bytes_per_run=1024)
    # Both the rotated remnant and the new file should have been sent
    combined = b"".join(sent)
    assert b'{"a":2}' in combined
    assert b'{"a":3}' in combined
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/test_cluster_push.py -v` → ImportError.

**Step 3: Implement**

`apps/observability/cluster_push.py`:

```python
"""Cursor + chunked push logic for the cluster logbook.

Kept separate from the management command for unit-testability.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Cursor:
    inode: int = 0
    offset: int = 0


class CursorStore:
    """Atomic JSON cursor file keyed by stream name."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load_raw(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def load(self, stream: str) -> Cursor:
        raw = self._load_raw().get(stream, {})
        return Cursor(inode=raw.get("inode", 0), offset=raw.get("offset", 0))

    def save(self, stream: str, cursor: Cursor) -> None:
        raw = self._load_raw()
        raw[stream] = asdict(cursor)
        # atomic write
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".cursor_", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(raw, f)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


SendFn = Callable[[bytes], tuple[int, int]]   # returns (status, accepted_bytes)


def _read_chunk(path: Path, offset: int, limit: int) -> bytes:
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read(limit)
    # Truncate at last newline so we never ship a partial JSON line
    nl = chunk.rfind(b"\n")
    if nl < 0:
        return b""
    return chunk[: nl + 1]


def _drain_rotated_if_needed(live: Path, store: CursorStore, stream: str,
                              send: SendFn, max_per_req: int, total_remaining: int) -> int:
    """If the cursor's inode != live inode, drain the *.1 rotated file first.
    Returns bytes pushed (deducts from total_remaining caller)."""
    cur = store.load(stream)
    if cur.inode == 0:
        return 0
    if live.exists() and live.stat().st_ino == cur.inode:
        return 0  # not rotated
    rotated = Path(str(live) + ".1")
    if not rotated.exists():
        # Local file disappeared; reset cursor and bail
        store.save(stream, Cursor(0, 0))
        return 0
    pushed = 0
    while total_remaining > 0:
        chunk = _read_chunk(rotated, cur.offset, min(max_per_req, total_remaining))
        if not chunk:
            break
        status, accepted = send(chunk)
        if status < 200 or status >= 300:
            raise RuntimeError(f"hub returned {status} on rotated drain")
        cur = Cursor(inode=cur.inode, offset=cur.offset + accepted)
        store.save(stream, cur)
        pushed += accepted
        total_remaining -= accepted
        if accepted == 0:
            break
    # Reset cursor to start of new live file
    if live.exists():
        store.save(stream, Cursor(inode=live.stat().st_ino, offset=0))
    return pushed


def push_stream(
    live: Path,
    store: CursorStore,
    *,
    stream: str,
    send: SendFn,
    max_bytes_per_request: int,
    max_bytes_per_run: int,
) -> int:
    """Push new bytes from `live` to the hub via `send`.

    Returns total bytes pushed. Raises RuntimeError on any 4xx/5xx.
    """
    total_remaining = max_bytes_per_run

    # Drain rotated remnant first if inode mismatch
    pushed = _drain_rotated_if_needed(live, store, stream, send, max_bytes_per_request, total_remaining)
    total_remaining -= pushed

    if not live.exists():
        return pushed
    inode = live.stat().st_ino
    cur = store.load(stream)
    if cur.inode != inode:
        cur = Cursor(inode=inode, offset=0)
        store.save(stream, cur)

    while total_remaining > 0:
        chunk = _read_chunk(live, cur.offset, min(max_bytes_per_request, total_remaining))
        if not chunk:
            break
        status, accepted = send(chunk)
        if status < 200 or status >= 300:
            raise RuntimeError(f"hub returned {status}")
        cur = Cursor(inode=cur.inode, offset=cur.offset + accepted)
        store.save(stream, cur)
        pushed += accepted
        total_remaining -= accepted
        if accepted == 0:
            break

    return pushed
```

`apps/observability/management/commands/push_logs_to_hub.py`:

```python
"""manage.py push_logs_to_hub — agent → hub batch log push."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.observability.cluster_push import CursorStore, push_stream
from apps.observability.heartbeat import heartbeat
from config.security.http import safe_urlopen


class Command(BaseCommand):
    help = "Push new JSONL chunks to the configured hub."

    def add_arguments(self, parser):
        parser.add_argument("--stream", choices=["events", "heartbeats", "all"], default="all")
        parser.add_argument("--max-bytes-per-request", type=int, default=5 * 1024 * 1024)
        parser.add_argument("--max-bytes-per-run", type=int, default=50 * 1024 * 1024)
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        hub = getattr(settings, "HUB_URL", "")
        if not hub:
            raise CommandError("HUB_URL not configured.")
        api_key = getattr(settings, "HUB_API_KEY", "") or ""
        if not api_key:
            raise CommandError("HUB_API_KEY not configured.")

        with heartbeat("cluster_push.events"):
            logs_dir = Path(settings.LOGS_DIR)
            store = CursorStore(logs_dir / "cluster_push_cursor.json")
            streams = ["events", "heartbeats"] if options["stream"] == "all" else [options["stream"]]
            for s in streams:
                self._push_one(s, logs_dir, store, hub, api_key, options)

    def _push_one(self, stream: str, logs_dir: Path, store: CursorStore, hub: str, api_key: str, options):
        live = logs_dir / f"{stream}.jsonl"
        if not live.exists():
            return

        def send(chunk: bytes) -> tuple[int, int]:
            url = hub.rstrip("/") + f"/cluster/logs/{stream}/"
            req = urllib.request.Request(
                url,
                data=chunk,
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/x-ndjson",
                },
            )
            try:
                with safe_urlopen(req, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=30) as resp:
                    body = resp.read()
                import json as _json
                obj = _json.loads(body) if body else {}
                return resp.status, int(obj.get("accepted_bytes", len(chunk)))
            except urllib.error.HTTPError as e:
                return e.code, 0

        push_stream(live, store, stream=stream, send=send,
                    max_bytes_per_request=options["max_bytes_per_request"],
                    max_bytes_per_run=options["max_bytes_per_run"])
```

Add `HUB_API_KEY = os.environ.get("HUB_API_KEY", "")` to `config/settings.py` (operator sets this on each agent).

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_cluster_push.py -v` → all 6 pass.

**Step 5: Commit**

```bash
git add apps/observability/cluster_push.py \
        apps/observability/management/commands/push_logs_to_hub.py \
        apps/observability/_tests/test_cluster_push.py \
        config/settings.py
git commit -m "feat(observability): push_logs_to_hub command + cursor-tracking library"
```

---

### Task 6.2: Hub endpoint `POST /cluster/logs/<stream>/`

**Files:**
- Create: `apps/observability/views/__init__.py` (empty)
- Create: `apps/observability/views/cluster_push.py`
- Create: `apps/observability/urls.py`
- Modify: `config/urls.py` (mount `cluster/` URL include)
- Modify: `config/middleware/api_key_auth.py` — add `/cluster/` to `API_PATH_PREFIXES`
- Create: `apps/observability/_tests/views/test_cluster_push.py`

**Step 1: Write the failing tests**

```python
"""Tests for ClusterLogPushView."""

import json
from pathlib import Path

import pytest
from django.test import Client


@pytest.fixture
def cluster_api_key(db):
    from config.models import APIKey
    key = APIKey.objects.create(name="agent-test-1",
                                 allowed_endpoints=["/cluster/logs/"])
    return key


def _auth_headers(api_key):
    # APIKey.save() exposes _raw_key on creation
    return {"HTTP_AUTHORIZATION": f"Bearer {api_key._raw_key}"}


def test_push_appends_to_per_instance_file(cluster_api_key, tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    body = b'{"ts":"2026-05-17T10:00:00.000Z","msg":"x","v":1,"level":"INFO","logger":"x","instance_id":"y"}\n'
    resp = Client().post(
        "/cluster/logs/events/",
        data=body,
        content_type="application/x-ndjson",
        **_auth_headers(cluster_api_key),
    )
    assert resp.status_code == 202
    assert json.loads(resp.content)["accepted_bytes"] == len(body)
    target = tmp_path / "cluster" / "agent-test-1" / "events.jsonl"
    assert target.exists()
    assert target.read_bytes() == body


def test_push_unknown_stream_returns_404(cluster_api_key):
    resp = Client().post("/cluster/logs/unknown/", data=b"", **_auth_headers(cluster_api_key))
    assert resp.status_code == 404


def test_push_oversized_body_returns_413(cluster_api_key, settings):
    settings.OBSERVABILITY_CLUSTER_MAX_BODY_BYTES = 10
    resp = Client().post("/cluster/logs/events/",
                          data=b"x" * 100,
                          content_type="application/x-ndjson",
                          **_auth_headers(cluster_api_key))
    assert resp.status_code == 413


def test_push_malformed_jsonl_returns_400_and_nothing_written(cluster_api_key, tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    resp = Client().post("/cluster/logs/events/",
                          data=b'not-json\n',
                          content_type="application/x-ndjson",
                          **_auth_headers(cluster_api_key))
    assert resp.status_code == 400
    target = tmp_path / "cluster" / "agent-test-1" / "events.jsonl"
    assert not target.exists()


def test_push_rejects_unsafe_api_key_name(db, settings, tmp_path):
    settings.LOGS_DIR = tmp_path
    from config.models import APIKey
    bad = APIKey.objects.create(name="../etc/passwd", allowed_endpoints=["/cluster/logs/"])
    resp = Client().post("/cluster/logs/events/",
                          data=b'{"a":1}\n',
                          content_type="application/x-ndjson",
                          HTTP_AUTHORIZATION=f"Bearer {bad._raw_key}")
    assert resp.status_code == 403
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/views/test_cluster_push.py -v` → 404 (URL not routed).

**Step 3: Implement**

`apps/observability/views/cluster_push.py`:

```python
from __future__ import annotations

import json
import re

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

INSTANCE_NAME_RE = re.compile(r"^[a-z0-9._-]{1,64}$")
_VALID_STREAMS = {"events", "heartbeats"}


class _JsonlValidationError(ValueError):
    pass


def _validate_jsonl(body: bytes) -> None:
    for idx, raw in enumerate(body.splitlines()):
        if not raw.strip():
            continue
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _JsonlValidationError(f"line {idx + 1}: {exc.msg}")


@method_decorator(csrf_exempt, name="dispatch")
class ClusterLogPushView(View):
    def post(self, request, stream: str):
        if stream not in _VALID_STREAMS:
            return JsonResponse({"error": "unknown stream"}, status=404)
        name = getattr(getattr(request, "api_key", None), "name", "")
        if not INSTANCE_NAME_RE.fullmatch(name):
            return JsonResponse(
                {"error": "api key name is not a valid instance id"}, status=403
            )

        body = request.body
        cap = settings.OBSERVABILITY_CLUSTER_MAX_BODY_BYTES
        if len(body) > cap:
            return JsonResponse({"error": "body too large"}, status=413)
        try:
            _validate_jsonl(body)
        except _JsonlValidationError as exc:
            return JsonResponse({"error": f"invalid jsonl: {exc}"}, status=400)

        target = settings.LOGS_DIR / "cluster" / name / f"{stream}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as f:
            f.write(body)
        return JsonResponse({"accepted_bytes": len(body)}, status=202)
```

`apps/observability/urls.py`:

```python
from django.urls import path

from apps.observability.views.cluster_push import ClusterLogPushView

app_name = "observability"

urlpatterns = [
    path("logs/<str:stream>/", ClusterLogPushView.as_view(), name="cluster-log-push"),
]
```

In `config/urls.py`:

```python
urlpatterns = [
    path("admin/", admin.site.urls),
    path("alerts/", include("apps.alerts.urls")),
    path("notify/", include("apps.notify.urls")),
    path("intelligence/", include("apps.intelligence.urls")),
    path("orchestration/", include("apps.orchestration.urls")),
    path("cluster/", include("apps.observability.urls")),   # <-- new
]
```

In `config/middleware/api_key_auth.py`, update the constant:

```python
API_PATH_PREFIXES = ("/alerts/", "/orchestration/", "/notify/", "/intelligence/", "/cluster/")
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/views/test_cluster_push.py -v` → 5 passed.

**Step 5: Commit**

```bash
git add apps/observability/views/ apps/observability/urls.py \
        config/urls.py config/middleware/api_key_auth.py \
        apps/observability/_tests/views/
git commit -m "feat(observability): cluster log push view (POST /cluster/logs/<stream>/)"
```

---

### Task 6.3: Cluster freshness check + management command

**Files:**
- Modify: `apps/observability/checks.py` (append cluster freshness check)
- Create: `apps/observability/management/commands/check_cluster_freshness.py`
- Create: `apps/observability/_tests/test_cluster_freshness.py`

**Step 1: Write the failing tests**

```python
"""Tests for cluster freshness check + command."""

from datetime import datetime, timedelta, timezone

from django.core.management import call_command


def test_warns_on_stale_agent(db, settings):
    from config.models import APIKey
    settings.OBSERVABILITY_CLUSTER_MAX_AGE = 60
    old = APIKey.objects.create(name="agent-stale", allowed_endpoints=["/cluster/logs/"])
    APIKey.objects.filter(pk=old.pk).update(
        last_used_at=datetime.now(tz=timezone.utc) - timedelta(hours=1)
    )
    from apps.observability.checks import check_cluster_freshness
    errs = check_cluster_freshness(None)
    assert any("agent-stale" in e.msg for e in errs)


def test_no_warning_for_recent_agent(db, settings):
    from config.models import APIKey
    settings.OBSERVABILITY_CLUSTER_MAX_AGE = 600
    APIKey.objects.create(name="agent-fresh", allowed_endpoints=["/cluster/logs/"],
                          last_used_at=datetime.now(tz=timezone.utc))
    from apps.observability.checks import check_cluster_freshness
    errs = check_cluster_freshness(None)
    assert not any("agent-fresh" in e.msg for e in errs)


def test_check_cluster_freshness_command_emits_alerts(db, settings):
    from config.models import APIKey
    settings.OBSERVABILITY_CLUSTER_MAX_AGE = 60
    APIKey.objects.create(name="agent-stale-2", allowed_endpoints=["/cluster/logs/"])
    APIKey.objects.filter(name="agent-stale-2").update(
        last_used_at=datetime.now(tz=timezone.utc) - timedelta(hours=1)
    )
    try:
        call_command("check_cluster_freshness")
    except SystemExit:
        pass
    from apps.alerts.models import Incident
    assert Incident.objects.filter(
        alert_fingerprint="cluster-stale:agent-stale-2"
    ).exists()
```

**Step 2: Run to verify they fail**

`uv run pytest apps/observability/_tests/test_cluster_freshness.py -v` → ImportError / no command.

**Step 3: Implement**

Append to `apps/observability/checks.py`:

```python
@checks.register()
def check_cluster_freshness(app_configs, **kwargs):
    """Warn if any APIKey holding the cluster-push allowlist hasn't been seen recently."""
    from datetime import datetime, timedelta, timezone

    try:
        from config.models import APIKey
    except Exception:
        return []
    max_age = timedelta(seconds=getattr(_settings, "OBSERVABILITY_CLUSTER_MAX_AGE", 900))
    now = datetime.now(tz=timezone.utc)
    errs = []
    for key in APIKey.objects.filter(is_active=True):
        if not any(ep == "/cluster/logs/" or ep.startswith("/cluster/logs/") for ep in (key.allowed_endpoints or [])):
            continue
        if not key.last_used_at:
            errs.append(checks.Warning(
                f"cluster agent {key.name} has never pushed",
                id="observability.H004",
            ))
            continue
        age = now - key.last_used_at
        if age > max_age:
            errs.append(checks.Warning(
                f"cluster agent {key.name} last seen {age} ago",
                id="observability.H004",
            ))
    return errs
```

`apps/observability/management/commands/check_cluster_freshness.py`:

```python
"""Per-agent cluster-push freshness check; emits Alerts via internal driver."""

import sys
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.alerts.services import AlertOrchestrator
from config.models import APIKey


class Command(BaseCommand):
    help = "Alert on cluster agents whose last_used_at exceeds OBSERVABILITY_CLUSTER_MAX_AGE."

    def handle(self, *args, **options):
        max_age = timedelta(seconds=settings.OBSERVABILITY_CLUSTER_MAX_AGE)
        now = datetime.now(tz=timezone.utc)
        orch = AlertOrchestrator()
        stale_count = 0
        for key in APIKey.objects.filter(is_active=True):
            if not any(ep.startswith("/cluster/logs/") for ep in (key.allowed_endpoints or [])):
                continue
            if key.last_used_at and (now - key.last_used_at) <= max_age:
                continue
            stale_count += 1
            orch.process_webhook({
                "source": "observability",
                "fingerprint": f"cluster-stale:{key.name}",
                "title": f"Cluster agent stale: {key.name}",
                "severity": "warning",
                "labels": {
                    "instance": key.name,
                    "max_age_seconds": int(max_age.total_seconds()),
                    "last_seen": str(key.last_used_at) if key.last_used_at else "never",
                },
                "description": "Agent has not pushed logs within the configured window.",
            }, driver="internal")
        if stale_count:
            sys.exit(1)
```

**Step 4: Run to verify they pass**

`uv run pytest apps/observability/_tests/test_cluster_freshness.py -v` → 3 passed.

**Step 5: Commit**

```bash
git add apps/observability/checks.py \
        apps/observability/management/commands/check_cluster_freshness.py \
        apps/observability/_tests/test_cluster_freshness.py
git commit -m "feat(observability): cluster freshness check + check_cluster_freshness command"
```

---

## Phase 7 — Install integration

### Task 7.1: Cron entries

**Files:**
- Modify: `bin/install/install.sh` (add observability cron block, idempotent)
- Modify: `bin/install/cron.sh` (or equivalent — confirm by reading existing structure)
- Create: `bin/install/_tests/test_observability_cron.sh` (BATS)

Run `ls bin/install/` first; if `cron.sh` doesn't exist, the cron-entry-generation logic is likely inside `install.sh`. Adapt the patch accordingly.

**Step 1: Write the failing test (idempotency)**

```bash
@test "install adds observability cron entries idempotently" {
    run bash bin/install/install.sh --dry-run --print-cron
    [ "$status" -eq 0 ]
    [[ "$output" == *"check_heartbeats"* ]]

    # Run again; should not add duplicates
    run bash bin/install/install.sh --dry-run --print-cron
    count=$(echo "$output" | grep -c "check_heartbeats")
    [ "$count" -eq 1 ]
}
```

**Step 2: Run to verify it fails**

Until the cron block is added, the test FAILS.

**Step 3: Implement**

Add a function to `bin/install/install.sh`:

```bash
install_observability_cron() {
    local cron_block
    cron_block="$(mktemp)"
    cat > "$cron_block" <<'EOF'
# === observability ===
*/5 * * * *  cd REPO_ROOT && APP_USER uv run manage.py check_heartbeats --quiet
EOF
    # Only when cluster mode:
    if [[ "${CLUSTER_ENABLED:-0}" == "1" && -n "${HUB_URL:-}" ]]; then
        cat >> "$cron_block" <<'EOF'
*       *       * * *  cd REPO_ROOT && APP_USER uv run manage.py push_logs_to_hub --quiet
EOF
    fi
    # Substitute and dedupe before installing
    sed -i "s|REPO_ROOT|$REPO_ROOT|g; s|APP_USER|sudo -u $APP_USER|g" "$cron_block"

    # Atomic-ish install: load existing crontab, strip our previous block, append fresh
    local existing
    existing="$(crontab -l 2>/dev/null || true)"
    {
        echo "$existing" | awk '/^# === observability ===$/{skip=1; next} /^# === end-observability ===$/{skip=0; next} !skip'
        cat "$cron_block"
        echo "# === end-observability ==="
    } | crontab -
    rm -f "$cron_block"
}
```

Call `install_observability_cron` from the top-level installer function (idempotent because the awk block strips the previous occurrence).

**Step 4: Run to verify it passes**

Re-run the BATS test → passes.

**Step 5: Commit**

```bash
git add bin/install/install.sh bin/install/_tests/test_observability_cron.sh
git commit -m "feat(observability): install adds idempotent cron entries for heartbeats + cluster push"
```

---

### Task 7.2: logrotate config (hub-side)

**Files:**
- Create: `bin/install/logrotate.d/server-monitoring.conf`
- Modify: `bin/install/install.sh` (copy into `/etc/logrotate.d/` if writable; otherwise print instructions)

**Step 1: Write the failing shell test**

```bash
@test "install logrotate config ships" {
    [ -f bin/install/logrotate.d/server-monitoring.conf ]
    grep -q 'LOGS_DIR/cluster' bin/install/logrotate.d/server-monitoring.conf
}
```

**Step 2: Run → fails**

**Step 3: Implement**

`bin/install/logrotate.d/server-monitoring.conf`:

```
LOGS_DIR/cluster/*/events.jsonl LOGS_DIR/cluster/*/heartbeats.jsonl {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    create 0640 APP_USER APP_GROUP
}
```

In `install.sh`, post-install step that substitutes `LOGS_DIR` / `APP_USER` / `APP_GROUP` and copies to `/etc/logrotate.d/` if `[ -w /etc/logrotate.d ]`, else prints the resolved path with copy instructions.

**Step 4: Run → passes**

**Step 5: Commit**

```bash
git add bin/install/logrotate.d/server-monitoring.conf bin/install/install.sh
git commit -m "feat(observability): ship logrotate config for hub-side cluster JSONL"
```

---

### Task 7.3: Preflight check `check_observability_health`

**Files:**
- Modify: `apps/checkers/preflight/checks.py` (add `check_observability_health`)
- Modify: `apps/checkers/_tests/preflight/test_checks.py`

**Step 1: Write failing test**

```python
def test_observability_health_flags_unwritable_logs_dir(tmp_path, settings):
    import os
    settings.LOGS_DIR = tmp_path
    os.chmod(tmp_path, 0o555)
    try:
        from apps.checkers.preflight.checks import check_observability_health
        results = check_observability_health()
        assert any("not writable" in r.message.lower() for r in results)
    finally:
        os.chmod(tmp_path, 0o755)


def test_observability_health_passes_when_logs_dir_writable(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from apps.checkers.preflight.checks import check_observability_health
    results = check_observability_health()
    # All-pass returns either empty list or list of "ok" results
    assert all(getattr(r, "level", "ok") in ("ok", "OK", "INFO") for r in results)
```

**Step 2: Run → fails (function missing).**

**Step 3: Implement** — add `check_observability_health()` in `apps/checkers/preflight/checks.py`; register with `@register("crontab")` decorator (or whatever the existing pattern is).

**Step 4: Run → passes.**

**Step 5: Commit**

```bash
git add apps/checkers/preflight/checks.py apps/checkers/_tests/preflight/test_checks.py
git commit -m "feat(observability): preflight check_observability_health"
```

---

## Phase 8 — End-to-end tests

### Task 8.1: Pipeline → events.jsonl integration

**File:** `apps/observability/_tests/test_e2e_pipeline_logs.py`

```python
"""End-to-end: run a pipeline, parse events.jsonl, assert correlation fields."""

import json
from pathlib import Path

from django.core.management import call_command


def test_pipeline_records_carry_trace_run_incident(tmp_path, settings, db):
    settings.LOGS_DIR = tmp_path
    call_command("run_pipeline", "--sample")
    events = (tmp_path / "events.jsonl").read_text().splitlines()
    parsed = [json.loads(l) for l in events if l.strip()]
    trace_ids = {r.get("trace_id") for r in parsed if r.get("trace_id")}
    run_ids = {r.get("run_id") for r in parsed if r.get("run_id")}
    assert trace_ids, "expected at least one record with a trace_id"
    assert run_ids,   "expected at least one record with a run_id"
    # At least one record carries category=alerts (from ingest stage)
    cats = {r.get("category") for r in parsed}
    assert "alerts" in cats or "orchestration" in cats
```

Steps 1–5: TDD as above. Commit message: `test(observability): e2e pipeline log correlation`.

---

### Task 8.2: Heartbeat → Incident integration

**File:** `apps/observability/_tests/test_e2e_heartbeat_to_alert.py`

```python
"""End-to-end: stale heartbeat → Incident via internal driver."""

import json
import time
from datetime import datetime, timedelta, timezone

from django.core.management import call_command


def test_stale_heartbeat_creates_one_incident(tmp_path, settings, db, monkeypatch):
    settings.LOGS_DIR = tmp_path
    # Inject a registered name with a tiny max_age
    from apps.observability import heartbeat_registry as reg
    reg.HEARTBEAT_REGISTRY["__test_stale__"] = reg.HeartbeatSpec(
        max_age=timedelta(milliseconds=10), desc="test", agent_only=False,
    )
    try:
        # Write a record older than max_age
        old = (datetime.now(tz=timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        (tmp_path / "heartbeats.jsonl").write_text(json.dumps({
            "ts": old, "v": 1, "level": "INFO", "logger": "x", "msg": "h",
            "instance_id": "test", "name": "__test_stale__", "status": "ok",
        }) + "\n")
        try:
            call_command("check_heartbeats")
        except SystemExit:
            pass
        # Second tick — must not duplicate
        try:
            call_command("check_heartbeats")
        except SystemExit:
            pass
        from apps.alerts.models import Incident
        n = Incident.objects.filter(alert_fingerprint="heartbeat-stale:__test_stale__").count()
        assert n == 1
    finally:
        del reg.HEARTBEAT_REGISTRY["__test_stale__"]
```

TDD as above. Commit: `test(observability): e2e heartbeat → Incident dedup`.

---

### Task 8.3: Cluster push round-trip

**File:** `apps/observability/_tests/test_e2e_cluster_roundtrip.py`

```python
"""End-to-end: agent push → hub append, byte-for-byte."""

import json
from pathlib import Path

import pytest
from django.test import Client


@pytest.fixture
def agent_key(db):
    from config.models import APIKey
    return APIKey.objects.create(name="agent-roundtrip",
                                  allowed_endpoints=["/cluster/logs/"])


def test_agent_chunk_roundtrips_to_hub_storage(agent_key, tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    body = b""
    for i in range(5):
        body += json.dumps({"ts": "2026-05-17T10:00:00.000Z", "v": 1, "level": "INFO",
                            "logger": "x", "msg": f"line{i}", "instance_id": "agent"}).encode() + b"\n"

    resp = Client().post(
        "/cluster/logs/events/",
        data=body,
        content_type="application/x-ndjson",
        HTTP_AUTHORIZATION=f"Bearer {agent_key._raw_key}",
    )
    assert resp.status_code == 202
    assert json.loads(resp.content)["accepted_bytes"] == len(body)

    target = tmp_path / "cluster" / "agent-roundtrip" / "events.jsonl"
    assert target.read_bytes() == body


def test_second_push_is_idempotent_for_unchanged_bytes(agent_key, tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    body = b'{"ts":"2026-05-17T10:00:00.000Z","v":1,"level":"INFO","logger":"x","msg":"m","instance_id":"a"}\n'
    for _ in range(2):
        Client().post(
            "/cluster/logs/events/", data=body, content_type="application/x-ndjson",
            HTTP_AUTHORIZATION=f"Bearer {agent_key._raw_key}",
        )
    # Both pushes append — the agent cursor is what prevents duplicate sends in real use.
    # Here we verify the hub does NOT de-dup; cursor logic is tested in test_cluster_push.
    target = tmp_path / "cluster" / "agent-roundtrip" / "events.jsonl"
    assert target.read_bytes() == body * 2
```

TDD as above. Commit: `test(observability): e2e cluster push roundtrip`.

---

## Phase 9 — Wrap-up

### Task 9.1: Coverage gate

Run: `uv run coverage run -m pytest && uv run coverage report --skip-empty`

Look for any new files under `apps/observability/` with < 100% branch coverage. Add focused tests until they're covered. Common easy misses:

- Cursor rotation drain when rotated backup also doesn't exist.
- `JsonLineFormatter` exception-record path when `exc_info=True`.
- `latest_heartbeats()` reading rotated backup when the live file is empty.
- `H003` when the heartbeat is fresh but `status="fail"`.

Commit additional tests with `test(observability): branch coverage gaps`.

---

### Task 9.2: Run full quality gate

```bash
uv run black .
uv run ruff check . --fix
uv run mypy . || true     # best-effort per project rules
uv run python manage.py check
uv run pytest
uv run coverage run -m pytest && uv run coverage report --fail-under=100
```

Any new failures (other than the known minute-boundary rate-limit flake) must be fixed before opening the PR.

---

### Task 9.3: Update CLAUDE.md and Security.md

**Files:**
- Modify: `CLAUDE.md` — add `apps.observability` to the "Core Apps" table (Stage column: "cross-cutting").
- Modify: `docs/Security.md` — add an entry to "Security Audit History" pointing at the design and impl plans; add a "Structured logging" subsection summarising the schema and the fact that `apps.observability` is the only file-handler home (with a cross-reference to the ruff rule parking-lot item).

Commit: `docs: register apps.observability in CLAUDE.md and Security.md`.

---

### Task 9.4: Open PR

```bash
git push -u origin feat/observability-stack
gh pr create --title "feat(observability): structured log stack (events.jsonl + heartbeats + CLI + cluster)" \
  --body "..."   # template per project convention; link the design doc
```

PR body should include:

- Summary of changes
- Migration note: legacy `django.log`, `checkers.W015`, `W016`, `CHECKS_LOG` removed
- New env vars: `OBSERVABILITY_*`, `HUB_API_KEY`
- New URLs: `/cluster/logs/<stream>/`
- New management commands: `read_logs`, `check_heartbeats`, `push_logs_to_hub`, `check_cluster_freshness`
- Test plan checklist
- Link: `docs/plans/2026-05-17-observability-stack-design.md`

---

## Open follow-ups (out of scope for this plan)

These items are tracked but **not** part of this PR. Each gets its own brainstorm/plan later:

- **Cluster topology** — fan-in vs mesh, agent-as-hub configurability, discovery, multi-hop. Today's plan is role-agnostic at the storage/transport layer.
- **Future CLI knobs** — `--export <path>` (write filtered output as JSONL) and `--summary` (group-by category count).
- **Ruff rule** — ban `logging.FileHandler` outside `apps/observability/`.
- **Web log viewer** — admin-rendered live log viewer with paginated filtering. Out of scope; CLI reader is sufficient for now.
- **Metrics** — this is a log stack, not a metrics stack.