"""One typed setting a console form offers, declared by the code that reads it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KINDS = ("text", "url", "int", "float", "bool", "choice", "list", "template", "headers")


@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    kind: str
    required: bool = False
    secret: bool = False
    default: Any = None
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Unknown config field kind {self.kind!r} for {self.key!r}")
        if self.kind == "headers" and not self.secret:
            raise ValueError(f"Headers field {self.key!r} must be secret")
