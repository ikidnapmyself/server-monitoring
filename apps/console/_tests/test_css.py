"""Contrast guard on console.css. A palette tweak must not drop text below AA."""

import re
from pathlib import Path

import pytest
from django.conf import settings

CSS = Path(settings.BASE_DIR) / "static" / "console" / "console.css"
DARK_MARKER = "@media (prefers-color-scheme: dark)"
TONES = ("critical", "warning", "info", "ok", "muted")
MIN_CONTRAST = 4.5


def _tokens(theme: str) -> dict[str, str]:
    light, dark = CSS.read_text().split(DARK_MARKER)
    block = dark.split("}")[0] if theme == "dark" else light.split("}")[0]
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", block))


def _luminance(hex_colour: str) -> float:
    parts = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    lighter, darker = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


PAIRS = [
    ("--text", "--bg"),
    ("--text", "--surface"),
    ("--text-muted", "--bg"),
    ("--text-muted", "--surface"),
    ("--accent", "--bg"),
    ("--accent", "--surface"),
    *[(f"--tone-{tone}-fg", f"--tone-{tone}-bg") for tone in TONES],
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), PAIRS)
def test_text_pairs_meet_aa(theme, fg, bg):
    tokens = _tokens(theme)

    assert _contrast(tokens[fg], tokens[bg]) >= MIN_CONTRAST


def test_both_themes_define_the_same_tokens():
    assert _tokens("light").keys() == _tokens("dark").keys()
