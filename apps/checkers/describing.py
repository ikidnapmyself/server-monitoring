"""Check results in words, from the entries a diagnose stage snapshot stored.

Snapshots come from old rows and other nodes too, so every value is read defensively and
nothing here raises on a shape it doesn't expect.
"""

from collections.abc import Mapping
from typing import Any

from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus, checker_label
from config.admin_badges import CHECK_STATUS_TONES
from config.describing import Description, Fact


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


def describe_check_errors(errors: tuple[str, ...]) -> Description | None:
    if not errors:
        return None
    count = len(errors)
    return Description(
        headline=f"The checks hit {count} error{'' if count == 1 else 's'}",
        tone="warning",
        facts=tuple(Fact("Error", error) for error in errors),
    )
