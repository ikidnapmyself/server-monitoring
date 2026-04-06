"""Unified preflight checks — one command, one output, everything visible."""

from dataclasses import dataclass


@dataclass
class CheckResult:
    """Result from a preflight check."""

    level: str  # "ok", "info", "warn", "error"
    message: str
    hint: str = ""
