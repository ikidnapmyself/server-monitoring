"""The settings a notify driver reads from its channel config, declared for the console form."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KINDS = ("text", "url", "int", "bool", "choice", "list", "template", "headers")


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


def timeout_field() -> ConfigField:
    return ConfigField("timeout", "Timeout (seconds)", "int", default=30)


COMMON_FIELDS = (
    ConfigField(
        "channel",
        "Default destination",
        "text",
        help="Where messages go when the alert names none.",
    ),
    ConfigField("template", "Message template", "template"),
    ConfigField("html_template", "HTML template", "template"),
    ConfigField(
        "payload_template",
        "Payload template",
        "template",
        help="A JSON body to send in place of the built one.",
    ),
)
