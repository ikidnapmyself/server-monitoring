"""Colour lives in ops.css. This is what keeps it there.

Without this, the next person adding a status column reaches for
``format_html('<span style="color:#dc3545">')``, because that is what the
neighbouring code used to look like.

The boundary is every file that can paint an admin page: each template under
``templates/``, every admin module under ``apps/`` and ``config/``, and the
projection modules those templates render. It stops at two places on purpose.
``static/admin/css/ops.css`` is where the hexes are supposed to be. And
``apps/notify/drivers/`` holds hexes that are Slack attachment colours sent over
the wire, not paint on a page this project renders.

What counts as an offence is a CSS colour declaration: any property whose name
ends in ``color``, plus ``background``, ``border``, ``outline``, ``box-shadow``,
``fill`` and ``stroke``, carrying a hex. A bare ``style="..."`` attribute holding
a hex under any property at all counts too, so inventing a property is not a way
round. So does an SVG presentation attribute, ``fill="#d33"`` on a sparkline
marker being the last hex the sweep had to move: a generated chart's paint is
still paint, and it has to change with the theme like everything else.
"""

import re
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)

# HTML numeric entities are stripped before matching. "&#9888;" is the warning
# triangle this admin prints in several places, and its digits read as a hex
# colour to anything scanning for "#" followed by hex digits.
ENTITY = re.compile(r"&#x?[0-9a-fA-F]+;")

HEX = r"#[0-9a-fA-F]{3,8}"

DECLARATION = re.compile(
    r"(?:[a-z]+-)*(?:color|background|border|outline|box-shadow|fill|stroke)"
    rf"\s*:\s*[^;\"'{{}}]*{HEX}"
)

STYLE_ATTR = re.compile(rf"style\s*=\s*([\"'])[^\"']*{HEX}[^\"']*\1")

# fill="#d33" on a generated <circle>: a colour, just not a declaration.
SVG_ATTR = re.compile(rf"(?:fill|stroke|stop-color|flood-color)\s*=\s*([\"']){HEX}\1")


def _scanned() -> list[Path]:
    """Every file colour could reach an admin page from."""
    paths = {
        *(ROOT / "templates").rglob("*.html"),
        *(ROOT / "apps").rglob("admin*.py"),
        *(ROOT / "apps").rglob("*_overview.py"),
        *(ROOT / "config").glob("admin*.py"),
        ROOT / "config" / "dashboard.py",
    }
    return sorted(path for path in paths if "_tests" not in path.parts)


SCANNED = _scanned()

# Named so a rename or a move fails loudly here instead of silently dropping out
# of the scan. Every one of these has held a hex at some point in the sweep.
MUST_BE_SCANNED = {
    "templates/admin/policy_overview.html",
    "templates/admin/dashboard.html",
    "templates/admin/map.html",
    "templates/admin/alerts/node/change_form.html",
    "apps/alerts/admin.py",
    "apps/checkers/admin.py",
    "apps/checkers/admin_charts.py",
    "apps/intelligence/admin.py",
    "apps/notify/admin.py",
    "apps/orchestration/admin.py",
    "apps/alerts/node_overview.py",
    "apps/alerts/policy_overview.py",
    "config/admin.py",
    "config/admin_badges.py",
    "config/dashboard.py",
}


def offences(text: str) -> list[str]:
    """The colour declarations in one file's text, entities discounted."""
    stripped = ENTITY.sub("", text)
    return DECLARATION.findall(stripped) + [
        m.group(0) for pattern in (STYLE_ATTR, SVG_ATTR) for m in pattern.finditer(stripped)
    ]


def _offenders() -> dict[str, list[str]]:
    found = {}
    for path in SCANNED:
        name = str(path.relative_to(ROOT))
        hits = offences(path.read_text())
        if hits:
            found[name] = hits
    return found


def test_no_inline_colour_outside_the_stylesheet():
    assert _offenders() == {}, f"inline colour belongs in ops.css: {_offenders()}"


def test_the_guard_scans_the_files_it_names():
    scanned = {str(path.relative_to(ROOT)) for path in SCANNED}
    assert MUST_BE_SCANNED <= scanned, f"dropped out of the scan: {MUST_BE_SCANNED - scanned}"


def test_a_warning_entity_is_not_a_colour():
    # "&#9888;" is four hex digits behind a "#". It is the triangle glyph, not paint.
    assert offences('<span class="ops-tint ops-tint--warning">&#9888; why</span>') == []
    assert offences('format_html("&#9888; cannot deliver: {}", gap)') == []


def test_the_guard_catches_what_it_is_for():
    assert offences('<span style="color:#dc3545">x</span>')
    assert offences("  background-color: #6c757d;")
    assert offences(".x { border-left-color: #28a745; }")
    assert offences('<td style="letter-spacing:1px; outline:1px solid #fff">')
    assert offences('<circle cx="1" cy="2" r="2" fill="#d33"/>')
