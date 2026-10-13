"""Typed filters parsed from a console querystring."""

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.http import QueryDict

from apps.alerts.models import AlertSeverity, IncidentStatus

STATUS_CHOICES: dict[str, tuple[str, ...]] = {
    "active": (IncidentStatus.OPEN.value, IncidentStatus.ACKNOWLEDGED.value),
    **{status: (status,) for status in IncidentStatus.values},
    "all": (),
}
SEVERITY_CHOICES: tuple[str, ...] = ("", *AlertSeverity.values)
SINCE_CHOICES: dict[str, timedelta | None] = {
    "": None,
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

# Capped at 18 digits so the value always fits a 64-bit database integer.
_POSITIVE_INT = re.compile(r"[0-9]{1,18}")


def _positive_int(raw: str) -> int | None:
    if not _POSITIVE_INT.fullmatch(raw):
        return None
    return int(raw) or None


@dataclass(frozen=True)
class IncidentFilter:
    status: str = "active"
    severity: str = ""
    node: int | None = None
    since: str = ""
    page: int = 1
    dropped: tuple[str, ...] = ()

    @classmethod
    def parse(cls, params: QueryDict) -> "IncidentFilter":
        values: dict[str, str | int] = {}
        dropped = []
        for name, choices in (
            ("status", STATUS_CHOICES),
            ("severity", SEVERITY_CHOICES),
            ("node", None),
            ("since", SINCE_CHOICES),
            ("page", None),
        ):
            raw = params.get(name)
            if raw is None:
                continue
            value = _positive_int(raw) if choices is None else raw if raw in choices else None
            if value is None:
                dropped.append(name)
            else:
                values[name] = value
        return cls(**values, dropped=tuple(dropped))  # type: ignore[arg-type]

    def statuses(self) -> tuple[str, ...]:
        return STATUS_CHOICES[self.status]

    def cutoff(self, now: datetime) -> datetime | None:
        window = SINCE_CHOICES[self.since]
        return now - window if window else None

    def querystring(self, **changes: str | int | None) -> str:
        """``""`` or ``"?a=b"`` for this filter with ``changes`` applied.

        Changing anything other than ``page`` goes back to the first page.
        """
        if changes.keys() - {"page"}:
            changes = {"page": 1, **changes}
        target = replace(self, **changes)  # type: ignore[arg-type]
        default = IncidentFilter()
        pairs = [
            (name, getattr(target, name))
            for name in ("status", "severity", "node", "since", "page")
            if getattr(target, name) != getattr(default, name)
        ]
        return f"?{urlencode(pairs)}" if pairs else ""

    def with_node_dropped(self) -> "IncidentFilter":
        return replace(self, node=None, dropped=(*self.dropped, "node"))
