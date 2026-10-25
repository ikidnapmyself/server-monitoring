"""Preflight runs and their checks in words, from what ``manage.py preflight`` stored."""

from apps.checkers.models import PreflightCheck, PreflightRun
from config.describing import Description

STATUS_TONES = {"ok": "ok", "warn": "warning", "error": "critical"}
LEVEL_TONES = {"error": "critical", "warn": "warning", "info": "info", "ok": "ok"}
NO_MESSAGE = "A check with no message"


def _plural(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else word + 's'}"


def preflight_headline(status: str, warnings: int, errors: int) -> str:
    """A run's outcome from its stored status and counts. The readiness panel words it too."""
    if status == "ok":
        return "Preflight passed"
    if status == "error":
        found, word = errors, "error"
    elif status == "warn":
        found, word = warnings, "warning"
    else:
        return "Preflight stored a result this hub can't read"
    return f"Preflight found {_plural(found, word)}" if found else "Preflight didn't pass"


def describe_preflight_run(run: PreflightRun) -> Description:
    return Description(
        preflight_headline(run.overall_status, run.warnings, run.errors),
        STATUS_TONES.get(run.overall_status, "muted"),
    )


def describe_preflight_check(check: PreflightCheck) -> Description:
    """The message passes through as the preflight command wrote it on that machine."""
    return Description(
        check.message.strip() or NO_MESSAGE,
        LEVEL_TONES.get(check.level, "muted"),
        detail=check.hint.strip(),
    )
