"""JSON-line logger for preflight results.

Appends one JSON line per run to logs/checks.log.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from apps.checkers.preflight import CheckResult


def log_results(checks: list[CheckResult], log_path: Path) -> None:
    """Append a JSON-line entry for this preflight run."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": sum(1 for c in checks if c.level == "ok"),
        "warnings": sum(1 for c in checks if c.level == "warn"),
        "errors": sum(1 for c in checks if c.level == "error"),
        "info": sum(1 for c in checks if c.level == "info"),
        "checks": [{"level": c.level, "message": c.message, "hint": c.hint} for c in checks],
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass
