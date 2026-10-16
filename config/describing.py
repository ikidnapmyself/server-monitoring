"""What an app's output means, in words a sysadmin can act on.

Each app describes its own output with these types. The console and notifications render
them and never decode raw rows themselves. Describers return an intent, never a URL, so
nothing upstream imports ``apps.console``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from config.admin_badges import TONES


@dataclass(frozen=True)
class Action:
    text: str
    intent: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    copy: bool = False


@dataclass(frozen=True)
class Description:
    headline: str
    tone: str
    detail: str = ""
    action: Action | None = None
    facts: tuple[Fact, ...] = ()

    def __post_init__(self) -> None:
        if self.tone not in TONES:
            raise ValueError(f"unknown tone: {self.tone!r}")
        if not self.headline:
            raise ValueError("a description needs a headline")


def or_action(d: Description, action: Action) -> Description:
    """``d`` with ``action`` only when it has none: a navigation link never hides a fix."""
    if d.action is not None:
        return d
    return replace(d, action=action)


def format_percent(value: float) -> str:
    return f"{round(float(value), 1):g}%"


def format_size_mb(mb: float) -> str:
    if mb < 1024:
        return f"{mb:.0f} MB"
    if mb < 1024**2:
        return f"{mb / 1024:.1f} GB"
    return f"{mb / 1024**2:.1f} TB"


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    if seconds < 1:
        return f"{min(999, round(seconds * 1000))}ms"
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m {whole % 60:02d}s"
    if whole < 86400:
        return f"{whole // 3600}h {whole % 3600 // 60:02d}m"
    return f"{whole // 86400}d {whole % 86400 // 3600}h"


def format_list(items: Iterable[str], limit: int = 3) -> str:
    if isinstance(items, str):
        raise TypeError("format_list takes a list of names, not a string")
    items = list(items)
    if len(items) > limit:
        return f"{', '.join(items[:limit])} and {len(items) - limit} more"
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def facts_from(metrics: Mapping[str, Any]) -> tuple[Fact, ...]:
    return tuple(
        Fact(key.replace("_", " ").capitalize(), str(value))
        for key, value in metrics.items()
        if isinstance(value, (str, int, float, bool))
    )
