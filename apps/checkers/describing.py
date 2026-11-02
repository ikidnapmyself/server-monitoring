"""Check results in words, from the entries a diagnose stage snapshot stored.

Snapshots come from old rows and other nodes too, so every value is read defensively and
nothing here raises on a shape it doesn't expect.
"""

from collections.abc import Mapping
from typing import Any

from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus, checker_label
from config.describing import Description, Fact
from config.tones import CHECK_STATUS_TONES

PROBLEM_ORDER = (CheckStatus.CRITICAL, CheckStatus.UNKNOWN, CheckStatus.WARNING)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def check_status(value: object) -> CheckStatus:
    """The status a result stored. Anything unrecognised reads as UNKNOWN."""
    for status in CheckStatus:
        if status.value == value:
            return status
    return CheckStatus.UNKNOWN


def describe_check(
    name: str, status: CheckStatus, metrics: Mapping[str, Any], message: str
) -> Description:
    checker = CHECKER_REGISTRY.get(name)
    if checker is not None:
        return checker().describe(status, dict(metrics), message)
    text = message.strip()
    first = text.splitlines()[0] if text else ""
    return Description(
        headline=first or checker_label(name) or "Unnamed check",
        tone=CHECK_STATUS_TONES[status.value],
        detail=text if text != first else "",
        facts=(Fact("Described by", "the node"),),
    )


def describe_checks(snapshot: Any) -> dict[CheckStatus, list[Description]]:
    """Every result a diagnose snapshot stored, described and grouped by status."""
    found: dict[CheckStatus, list[Description]] = {status: [] for status in CheckStatus}
    checks = snapshot.get("checks") if isinstance(snapshot, dict) else None
    for check in checks if isinstance(checks, list) else ():
        if not isinstance(check, dict):
            continue
        status = check_status(check.get("status"))
        metrics = check.get("metrics")
        found[status].append(
            describe_check(
                _text(check.get("name")),
                status,
                metrics if isinstance(metrics, dict) else {},
                _text(check.get("message")),
            )
        )
    return found


def describe_findings(snapshot: Any) -> tuple[Description, ...]:
    """The problems a diagnose snapshot found, worst first."""
    found = describe_checks(snapshot)
    return tuple(d for status in PROBLEM_ORDER for d in found[status])


def describe_check_errors(errors: tuple[str, ...]) -> Description | None:
    if not errors:
        return None
    count = len(errors)
    return Description(
        headline=f"The checks hit {count} error{'' if count == 1 else 's'}",
        tone="warning",
        facts=tuple(Fact("Error", error) for error in errors),
    )
