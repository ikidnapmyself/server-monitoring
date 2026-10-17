"""Typed filters parsed from a console querystring."""

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from django.http import QueryDict

from apps.alerts.models import AlertSeverity, AlertStatus, IncidentStatus

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

ALERT_STATUS_CHOICES: dict[str, tuple[str, ...]] = {
    **{status: (status,) for status in AlertStatus.values},
    "all": (),
}

FIELDS = ("status", "severity", "node", "since", "page")
ALERT_FIELDS = ("q", "status", "severity", "node", "source", "since", "page")
SEARCH_LIMIT = 255
SOURCE_LIMIT = 100

Choices = dict[str, Any] | tuple[str, ...] | None

# Capped at 18 digits so the value always fits a 64-bit database integer.
_POSITIVE_INT = re.compile(r"[0-9]{1,18}")


def _positive_int(raw: str) -> int | None:
    if not _POSITIVE_INT.fullmatch(raw):
        return None
    return int(raw) or None


def _parse(
    params: QueryDict, specs: tuple[tuple[str, Choices], ...]
) -> tuple[dict[str, str | int], list[str]]:
    """Valid values by field name, and the names whose value was refused."""
    values: dict[str, str | int] = {}
    dropped = []
    for name, choices in specs:
        raw = params.get(name)
        if raw is None:
            continue
        value = _positive_int(raw) if choices is None else raw if raw in choices else None
        if value is None:
            dropped.append(name)
        else:
            values[name] = value
    return values, dropped


def _text(params: QueryDict, name: str, limit: int) -> str:
    # PostgreSQL refuses a NUL in text.
    return params.get(name, "").replace("\x00", "").strip()[:limit]


def _encode(
    target: "IncidentFilter | AlertFilter",
    default: "IncidentFilter | AlertFilter",
    fields: tuple[str, ...],
    changes: dict[str, Any],
) -> str:
    """``""`` or ``"?a=b"`` for ``target`` with ``changes`` applied.

    Changing anything other than ``page`` goes back to the first page.
    """
    if changes.keys() - {"page"}:
        changes = {"page": 1, **changes}
    target = replace(target, **changes)
    pairs = [
        (name, getattr(target, name))
        for name in fields
        if getattr(target, name) != getattr(default, name)
    ]
    return f"?{urlencode(pairs)}" if pairs else ""


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
        values, dropped = _parse(
            params,
            (
                ("status", STATUS_CHOICES),
                ("severity", SEVERITY_CHOICES),
                ("node", None),
                ("since", SINCE_CHOICES),
                ("page", None),
            ),
        )
        return cls(**values, dropped=tuple(dropped))  # type: ignore[arg-type]

    def statuses(self) -> tuple[str, ...]:
        return STATUS_CHOICES[self.status]

    def cutoff(self, now: datetime) -> datetime | None:
        window = SINCE_CHOICES[self.since]
        return now - window if window else None

    def querystring(self, **changes: str | int | None) -> str:
        return _encode(self, IncidentFilter(), FIELDS, changes)

    def with_node_dropped(self) -> "IncidentFilter":
        dropped = tuple(name for name in FIELDS if name == "node" or name in self.dropped)
        return replace(self, node=None, dropped=dropped)


@dataclass(frozen=True)
class AlertFilter:
    q: str = ""
    status: str = AlertStatus.FIRING.value
    severity: str = ""
    node: int | None = None
    source: str = ""
    since: str = ""
    page: int = 1
    dropped: tuple[str, ...] = ()

    @classmethod
    def parse(cls, params: QueryDict) -> "AlertFilter":
        values, dropped = _parse(
            params,
            (
                ("status", ALERT_STATUS_CHOICES),
                ("severity", SEVERITY_CHOICES),
                ("node", None),
                ("since", SINCE_CHOICES),
                ("page", None),
            ),
        )
        for name, limit in (("q", SEARCH_LIMIT), ("source", SOURCE_LIMIT)):
            text = _text(params, name, limit)
            if text:
                values[name] = text
        return cls(**values, dropped=tuple(dropped))  # type: ignore[arg-type]

    def statuses(self) -> tuple[str, ...]:
        return ALERT_STATUS_CHOICES[self.status]

    def cutoff(self, now: datetime) -> datetime | None:
        window = SINCE_CHOICES[self.since]
        return now - window if window else None

    def querystring(self, **changes: str | int | None) -> str:
        return _encode(self, AlertFilter(), ALERT_FIELDS, changes)

    def without(self, *names: str) -> "AlertFilter":
        default = AlertFilter()
        dropped = tuple(n for n in ALERT_FIELDS if n in names or n in self.dropped)
        return replace(self, **{n: getattr(default, n) for n in names}, dropped=dropped)

    def kept(self) -> tuple[tuple[str, str], ...]:
        """Filters a search submit must resend, since a GET form sends only its own fields."""
        default = AlertFilter()
        return tuple(
            (name, str(getattr(self, name)))
            for name in ("status", "severity", "node", "source", "since")
            if getattr(self, name) != getattr(default, name)
        )
