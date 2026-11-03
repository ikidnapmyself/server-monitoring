"""Which of five tones each domain status reads in.

A ``Description`` carries a tone name. The console's ``c-tone-<tone>`` classes in
``static/console/console.css`` give each tone its colour.
"""

TONES = frozenset({"critical", "warning", "info", "ok", "muted"})


# Keyed on the raw string values rather than the enum members: the apps import these
# tables, so importing an app enum here would close a config -> apps -> config cycle at
# module load. ``config/_tests/test_tones.py`` asserts each table still covers its enum.
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
    "skipped": "muted",
}


def tone_for(table: dict[str, str], value: str) -> str:
    """Tone for a domain status, falling back to ``muted`` for anything unmapped."""
    return table.get(value, "muted")
