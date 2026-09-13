"""Guards on static/admin/css/ops.css that only reading the file can provide.

Three failure modes, none of which a rendering test would catch:

1. A selector weakened below Django's own. base.css declares the light tokens under
   ``html[data-theme="light"], :root`` and dark_mode.css declares the dark ones under
   ``html[data-theme="dark"]``. Both are (0,1,1), so a bare ``[data-theme="dark"]`` at
   (0,1,0) loses regardless of load order and the skin silently reverts to Django's
   palette in that theme.
2. The two dark blocks drifting apart. The auto state cannot share a selector list
   with the explicit one, so the token set is written twice.
3. A palette tweak dropping a badge pair below the 4.5:1 the badge font size requires.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

OPS_CSS = Path(settings.BASE_DIR) / "static" / "admin" / "css" / "ops.css"

TONES = ("critical", "warning", "info", "ok", "muted")
MIN_CONTRAST = 4.5

LIGHT_SELECTOR = 'html[data-theme="light"],\n:root {'
DARK_SELECTOR = 'html[data-theme="dark"] {'
AUTO_SELECTOR = ':root:not([data-theme="light"]) {'


def _declarations(marker: str) -> dict[str, str]:
    """The ``name: value`` pairs of the one rule introduced by ``marker``."""
    text = OPS_CSS.read_text()
    start = text.find(marker)
    if start == -1:
        pytest.fail(f"marker {marker!r} not found in {OPS_CSS}")
    start += len(marker)

    depth = 1
    for end, char in enumerate(text[start:], start):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth == 0:
            break
    else:
        pytest.fail(f"unbalanced braces after {marker!r} in {OPS_CSS}")

    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", text[start:end]))


def _relative_luminance(hex_color: str) -> float:
    parts = [int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    lighter, darker = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_theme_selectors_match_djangos_own_weight():
    text = OPS_CSS.read_text()
    assert DARK_SELECTOR in text
    assert LIGHT_SELECTOR in text
    assert AUTO_SELECTOR in text


def test_auto_dark_declares_the_same_tokens_and_values_as_explicit_dark():
    explicit = _declarations(DARK_SELECTOR)
    auto = _declarations(AUTO_SELECTOR)

    assert explicit, f"no custom properties found under {DARK_SELECTOR!r}"
    assert explicit == auto


@pytest.mark.parametrize("marker", [LIGHT_SELECTOR, DARK_SELECTOR, AUTO_SELECTOR])
def test_every_theme_defines_every_tone(marker):
    declared = _declarations(marker)
    for tone in TONES:
        assert f"--ops-{tone}-fg" in declared
        assert f"--ops-{tone}-bg" in declared


@pytest.mark.parametrize("marker", [LIGHT_SELECTOR, DARK_SELECTOR, AUTO_SELECTOR])
@pytest.mark.parametrize("tone", TONES)
def test_badge_pairs_meet_wcag_aa(marker, tone):
    """Badges render at 11px, which is normal text, so AA is 4.5:1 and not 3:1."""
    declared = _declarations(marker)
    ratio = _contrast(declared[f"--ops-{tone}-fg"], declared[f"--ops-{tone}-bg"])
    assert ratio >= MIN_CONTRAST, f"{tone} is {ratio:.2f}:1"
