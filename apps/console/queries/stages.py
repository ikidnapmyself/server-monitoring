"""How a stage's stored output reads, for every page that shows one."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from apps.checkers.checkers import CheckStatus
from apps.checkers.describing import PROBLEM_ORDER, describe_check_errors, describe_checks
from apps.intelligence.describing import analysis_failed, describe_analysis
from apps.notify.describing import describe_notify
from config.describing import Description


@dataclass(frozen=True)
class CheckBody:
    kind: ClassVar[str] = "check"
    problems: tuple[Description, ...]
    ok_count: int
    skipped_count: int
    errors: Description | None
    raw: str


@dataclass(frozen=True)
class Recommendation:
    title: str
    priority: str
    description: str


@dataclass(frozen=True)
class AnalyzeBody:
    kind: ClassVar[str] = "analyze"
    outcome: Description
    actions: tuple[str, ...]
    recommendations: tuple[Recommendation, ...]
    raw: str


@dataclass(frozen=True)
class NotifyBody:
    kind: ClassVar[str] = "notify"
    deliveries: tuple[Description, ...]
    raw: str


@dataclass(frozen=True)
class RawBody:
    kind: ClassVar[str] = "raw"
    raw: str


StageBody = CheckBody | AnalyzeBody | NotifyBody | RawBody


@dataclass(frozen=True)
class TimelineEntry:
    id: str
    when: datetime
    kind: str
    what: Description
    body: StageBody | None


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for text in map(_text, value) if text)


def _dicts(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _check_body(snapshot: dict[str, Any], raw: str) -> CheckBody:
    found = describe_checks(snapshot)
    return CheckBody(
        problems=tuple(d for status in PROBLEM_ORDER for d in found[status]),
        ok_count=len(found[CheckStatus.OK]),
        skipped_count=len(found[CheckStatus.SKIPPED]),
        errors=describe_check_errors(_texts(snapshot.get("errors"))),
        raw=raw,
    )


def _analyze_body(snapshot: dict[str, Any], raw: str) -> AnalyzeBody:
    failed = analysis_failed(snapshot)
    return AnalyzeBody(
        outcome=describe_analysis(snapshot),
        actions=() if failed else _texts(snapshot.get("actions")),
        recommendations=(
            ()
            if failed
            else tuple(
                Recommendation(
                    title=_text(item.get("title")),
                    priority=_text(item.get("priority")),
                    description=_text(item.get("description")),
                )
                for item in _dicts(snapshot.get("recommendations"))
            )
        ),
        raw=raw,
    )


def _notify_body(snapshot: dict[str, Any], raw: str) -> NotifyBody:
    return NotifyBody(deliveries=describe_notify(snapshot), raw=raw)


_BODY_BUILDERS: dict[str, tuple[frozenset[str], Callable[[dict[str, Any], str], StageBody]]] = {
    "check": (
        frozenset({"checks", "checks_run", "checks_passed", "checks_failed"}),
        _check_body,
    ),
    "analyze": (
        frozenset(
            {
                "summary",
                "probable_cause",
                "actions",
                "recommendations",
                "confidence",
                "fallback_used",
                "failure",
            }
        ),
        _analyze_body,
    ),
    "notify": (
        frozenset({"deliveries", "channels_attempted", "channels_succeeded", "channels_failed"}),
        _notify_body,
    ),
}


def stage_body(stage: str, snapshot: object) -> StageBody | None:
    """Renderable form of a stage's stored output. The JSON is untrusted in shape.

    A snapshot that is not an object, or that carries none of its stage's keys, is
    shown raw. Old runs stored a different object under the same stage name.
    """
    if not snapshot:
        return None
    raw = json.dumps(snapshot, indent=2, sort_keys=True, default=str, ensure_ascii=False)
    keys, builder = _BODY_BUILDERS.get(stage, (frozenset(), None))
    if builder is None or not isinstance(snapshot, dict) or keys.isdisjoint(snapshot):
        return RawBody(raw=raw)
    return builder(snapshot, raw)
