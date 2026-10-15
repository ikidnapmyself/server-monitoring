"""Guards on console.css: text contrast stays at AA and every badge tone has a rule."""

import re
from pathlib import Path

import pytest
from django.conf import settings

from config.admin_badges import TONES

CSS = Path(settings.BASE_DIR) / "static" / "console" / "console.css"
DARK_MARKER = "@media (prefers-color-scheme: dark)"
MIN_CONTRAST = 4.5
STATES = ("success", "attention", "danger", "done", "accent")


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
    # Body text, muted text and links on the page, box headers and inset code.
    *[
        (fg, bg)
        for fg in ("--fgColor-default", "--fgColor-muted", "--fgColor-accent")
        for bg in ("--bgColor-default", "--bgColor-muted")
    ],
    ("--fgColor-default", "--bgColor-inset"),
    # Labels, row icons and readiness links in each state colour.
    *[
        (f"--fgColor-{state}", bg)
        for state in STATES
        for bg in ("--bgColor-default", "--bgColor-muted")
    ],
    # A linked label on hover.
    *[(f"--fgColor-{state}", f"--bgColor-{state}-muted") for state in STATES],
    # Flash text on each tone's background.
    *[("--fgColor-default", f"--bgColor-{state}-muted") for state in STATES],
    ("--fgColor-default", "--bgColor-neutral-muted"),
    # StateLabels, timeline lifecycle badges and the primary button.
    *[("--fgColor-onEmphasis", f"--bgColor-{state}-emphasis") for state in STATES],
    ("--fgColor-onEmphasis", "--bgColor-neutral-emphasis"),
    ("--fgColor-onEmphasis", "--button-primary-bgColor-rest"),
    ("--fgColor-onEmphasis", "--button-primary-bgColor-hover"),
    ("--fgColor-onEmphasis", "--button-danger-bgColor-hover"),
    ("--fgColor-default", "--button-default-bgColor-rest"),
    ("--fgColor-default", "--button-default-bgColor-hover"),
    ("--button-danger-fgColor-rest", "--button-default-bgColor-rest"),
    ("--fgColor-muted", "--button-default-bgColor-rest"),
    # Segmented control and underline nav.
    ("--fgColor-default", "--controlTrack-bgColor-rest"),
    ("--fgColor-default", "--controlTrack-bgColor-hover"),
    ("--fgColor-default", "--controlKnob-bgColor-rest"),
    ("--fgColor-muted", "--timelineBadge-bgColor"),
    # The header.
    ("--header-fgColor", "--header-bgColor"),
    ("--header-fgColor-logo", "--header-bgColor"),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), PAIRS)
def test_text_pairs_meet_aa(theme, fg, bg):
    tokens = _tokens(theme)

    assert _contrast(tokens[fg], tokens[bg]) >= MIN_CONTRAST


def test_both_themes_define_the_same_tokens():
    assert _tokens("light").keys() == _tokens("dark").keys()


def test_every_colour_token_is_a_six_digit_hex():
    light, dark = CSS.read_text().split(DARK_MARKER)
    for block in (light.split("}")[0], dark.split("}")[0]):
        declared = re.findall(r"(--[\w-]+)\s*:", block)
        assert len(declared) == len(_tokens("light"))


@pytest.mark.parametrize("tone", sorted(TONES))
def test_every_tone_has_a_rule(tone):
    assert re.search(rf"^\.c-tone-{tone}\s*\{{[^}}]*\bcolor\s*:", CSS.read_text(), re.M)
