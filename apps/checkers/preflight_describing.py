"""Preflight runs and their checks in words, from what ``manage.py preflight`` stored."""

from apps.checkers.models import PreflightCheck, PreflightRun
from config.describing import Description

LEVEL_TONES = {"error": "critical", "warn": "warning", "info": "info", "ok": "ok"}
NO_MESSAGE = "A check with no message"


def _plural(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else word + 's'}"


def describe_preflight_run(run: PreflightRun) -> Description:
    if run.overall_status == "ok":
        return Description("Preflight passed", "ok")
    if run.overall_status == "error":
        found, word, tone = run.errors, "error", "critical"
    elif run.overall_status == "warn":
        found, word, tone = run.warnings, "warning", "warning"
    else:
        return Description("Preflight stored a result this hub can't read", "muted")
    if not found:
        return Description("Preflight didn't pass", tone)
    return Description(f"Preflight found {_plural(found, word)}", tone)


def describe_preflight_check(check: PreflightCheck) -> Description:
    """The message passes through as the preflight command wrote it on that machine."""
    return Description(
        check.message.strip() or NO_MESSAGE,
        LEVEL_TONES.get(check.level, "muted"),
        detail=check.hint.strip(),
    )
