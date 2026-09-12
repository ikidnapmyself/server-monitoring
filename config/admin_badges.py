"""Status colour for the admin, in one place.

Every status pill and coloured status word in the admin goes through here. Colour
itself lives in ``static/admin/css/ops.css`` under five tone classes, so a theme
change is a stylesheet edit rather than a sweep through five ``admin.py`` files.
"""

from django.utils.html import format_html
from django.utils.safestring import SafeString

TONES = frozenset({"critical", "warning", "info", "ok", "muted"})


def _check(tone: str) -> None:
    if tone not in TONES:
        raise ValueError(f"unknown badge tone: {tone!r}")


def badge(text: object, tone: str, *, url: str | None = None) -> SafeString:
    """A status pill. Renders as an anchor when ``url`` is given."""
    _check(tone)
    if url is None:
        return format_html('<span class="ops-badge ops-badge--{}">{}</span>', tone, text)
    return format_html('<a class="ops-badge ops-badge--{}" href="{}">{}</a>', tone, url, text)


def tinted(text: object, tone: str) -> SafeString:
    """Coloured inline text with no pill. For glyph strips and inline warnings."""
    _check(tone)
    return format_html('<span class="ops-tint ops-tint--{}">{}</span>', tone, text)
