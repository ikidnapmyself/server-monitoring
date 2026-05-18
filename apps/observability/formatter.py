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
# Reserved LogRecord keys (standard fields) AND fields we promote to top-level
# (category, trace_id, run_id, incident_id, stage, source). Caller-supplied
# `extra={"trace_id": ...}` is silently dropped — context wins. Tested below.
_RESERVED_RECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        # Fields we already promote out of extra into top-level keys
        "category",
        "trace_id",
        "run_id",
        "incident_id",
        "stage",
        "source",
    }
)

# Logger-prefix -> category mapping. First matching prefix wins.
_CATEGORY_MAP: tuple[tuple[str, str], ...] = (
    ("apps.observability.heartbeat", "heartbeat"),
    ("apps.observability", "observability"),
    ("apps.alerts", "alerts"),
    ("apps.checkers", "checkers"),
    ("apps.intelligence", "intelligence"),
    ("apps.notify", "notify"),
    ("apps.orchestration", "orchestration"),
    ("config.middleware", "http"),
    ("config", "internal"),
    ("apps", "internal"),
)


def _resolve_category(logger_name: str, record_dict: dict) -> str:
    if "category" in record_dict and record_dict["category"]:
        return str(record_dict["category"])
    for prefix, cat in _CATEGORY_MAP:
        if logger_name == prefix or logger_name.startswith(prefix + "."):
            return cat
    return "internal"


_INSTANCE_ID: str | None = None


def _resolve_instance_id() -> str:
    global _INSTANCE_ID
    if _INSTANCE_ID is None:
        _INSTANCE_ID = getattr(settings, "INSTANCE_ID", "") or socket.gethostname()
    return _INSTANCE_ID


def _utc_iso(ts: float) -> str:
    # millisecond precision, trailing Z
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{int((ts % 1) * 1000):03d}Z"
    )


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line, newline-terminated."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": _utc_iso(record.created),
            "v": SCHEMA_VERSION,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "instance_id": _resolve_instance_id(),
        }

        # ContextVars -> top-level fields (only when set)
        for name, value in context.snapshot().items():
            if value is not None:
                obj[name] = value

        # Category
        obj["category"] = _resolve_category(record.name, record.__dict__)

        # Extra keys: anything on record.__dict__ that's not a reserved key
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED_RECORD_KEYS}
        if extra:
            obj["extra"] = extra

        # Exception info
        if record.exc_info:
            exc_type, exc_val, exc_tb = record.exc_info
            obj["exc_type"] = exc_type.__name__ if exc_type else ""
            obj["exc_msg"] = str(exc_val) if exc_val else ""
            obj["exc_stack"] = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))

        return json.dumps(obj, default=str, ensure_ascii=False)


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
