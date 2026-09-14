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


# Keyed on the raw string values rather than the enum members: ``config`` is imported
# by every ``apps/*/admin.py``, so importing an app enum here would close a
# config -> apps -> config cycle at module load. ``config/_tests`` asserts each table
# still covers its enum.
SEVERITY_TONES = {"critical": "critical", "warning": "warning", "info": "info"}

ALERT_STATUS_TONES = {"firing": "critical", "resolved": "ok"}

INCIDENT_STATUS_TONES = {
    "open": "critical",
    "acknowledged": "warning",
    "resolved": "ok",
    "closed": "muted",
}

CHECK_STATUS_TONES = {
    "ok": "ok",
    "warning": "warning",
    "critical": "critical",
    "unknown": "muted",
}

STAGE_STATUS_TONES = {
    "pending": "muted",
    "running": "warning",
    "succeeded": "ok",
    "failed": "critical",
    "retrying": "warning",
    "skipped": "muted",
}

DIAGNOSIS_TONES = {
    "ok": "ok",
    "empty": "warning",
    "failed": "critical",
    "stalled": "warning",
    "skipped": "muted",
    "never_ran": "critical",
}


def tone_for(table: dict[str, str], value: str) -> str:
    """Tone for a domain status, falling back to ``muted`` for anything unmapped."""
    return table.get(value, "muted")
