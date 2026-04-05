"""System status checks — cross-source configuration consistency."""

from dataclasses import dataclass


@dataclass
class CheckResult:
    """Result from a status check."""

    level: str  # "ok", "info", "warn", "error"
    message: str
    hint: str = ""
    category: str = ""  # "env", "cluster", "runtime", "database", "installation"
