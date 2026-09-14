"""The single source of admin status colour."""

import pytest
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.alerts.diagnosis import StageDiag
from apps.alerts.models import AlertSeverity, AlertStatus, IncidentStatus
from apps.checkers.models import CheckStatus
from apps.orchestration.models import StageStatus
from config.admin_badges import (
    ALERT_STATUS_TONES,
    CHECK_STATUS_TONES,
    DIAGNOSIS_TONES,
    INCIDENT_STATUS_TONES,
    SEVERITY_TONES,
    STAGE_STATUS_TONES,
    TONES,
    badge,
    tinted,
    tone_for,
)

_ALL_TABLES = {
    "severity": SEVERITY_TONES,
    "alert_status": ALERT_STATUS_TONES,
    "incident_status": INCIDENT_STATUS_TONES,
    "check": CHECK_STATUS_TONES,
    "stage": STAGE_STATUS_TONES,
    "diagnosis": DIAGNOSIS_TONES,
}


def test_badge_renders_its_tone_class():
    assert badge("OPEN", "critical") == '<span class="ops-badge ops-badge--critical">OPEN</span>'


def test_badge_returns_safe_string():
    assert isinstance(badge("OPEN", "ok"), SafeString)


def test_badge_escapes_its_text():
    # Alert names and hostnames arrive over a webhook.
    assert "&lt;script&gt;" in badge("<script>", "muted")


def test_badge_renders_a_non_string_text():
    assert badge(3, "info") == '<span class="ops-badge ops-badge--info">3</span>'


def test_badge_with_url_renders_an_anchor():
    assert badge("3 CRITICAL", "critical", url="/admin/x/?a=1") == (
        '<a class="ops-badge ops-badge--critical" href="/admin/x/?a=1">3 CRITICAL</a>'
    )


def test_badge_escapes_its_url():
    # A quote in the href must be entity-encoded, not close the attribute early.
    assert badge("x", "ok", url='/a/?b="c"') == (
        '<a class="ops-badge ops-badge--ok" href="/a/?b=&quot;c&quot;">x</a>'
    )


def test_badge_does_not_re_escape_safe_markup():
    assert badge(format_html("<b>{}</b>", 3), "ok") == (
        '<span class="ops-badge ops-badge--ok"><b>3</b></span>'
    )


def test_badge_rejects_an_unknown_tone():
    # Silently emitting an unclassed span is how a status goes invisible.
    with pytest.raises(ValueError, match="unknown badge tone"):
        badge("OPEN", "danger")


def test_badge_with_url_rejects_an_unknown_tone():
    with pytest.raises(ValueError, match="unknown badge tone"):
        badge("OPEN", "danger", url="/admin/x/")


def test_tinted_renders_its_tone_class():
    assert tinted("stalled", "warning") == '<span class="ops-tint ops-tint--warning">stalled</span>'


def test_tinted_escapes_its_text():
    assert "&lt;script&gt;" in tinted("<script>", "muted")


def test_tinted_does_not_re_escape_safe_markup():
    # Task 5 and Task 7 tint an already-formatted count.
    assert tinted(format_html("<b>{}</b>", 3), "info") == (
        '<span class="ops-tint ops-tint--info"><b>3</b></span>'
    )


def test_tinted_returns_safe_string():
    assert isinstance(tinted("stalled", "ok"), SafeString)


def test_tinted_rejects_an_unknown_tone():
    with pytest.raises(ValueError, match="unknown badge tone"):
        tinted("x", "purple")


def test_tones_are_the_five_documented_ones():
    assert TONES == frozenset({"critical", "warning", "info", "ok", "muted"})


def test_tone_for_maps_a_known_value():
    assert tone_for(INCIDENT_STATUS_TONES, "acknowledged") == "warning"


def test_tone_for_falls_back_to_muted():
    # An unmapped status renders grey rather than raising in a list column.
    assert tone_for(INCIDENT_STATUS_TONES, "wat") == "muted"


@pytest.mark.parametrize("table", _ALL_TABLES.values(), ids=_ALL_TABLES.keys())
def test_every_mapped_tone_is_a_known_tone(table):
    assert set(table.values()) <= TONES


@pytest.mark.parametrize(
    ("table", "enum"),
    [
        (SEVERITY_TONES, AlertSeverity),
        (ALERT_STATUS_TONES, AlertStatus),
        (INCIDENT_STATUS_TONES, IncidentStatus),
        (CHECK_STATUS_TONES, CheckStatus),
        (STAGE_STATUS_TONES, StageStatus),
        (DIAGNOSIS_TONES, StageDiag),
    ],
    ids=["severity", "alert_status", "incident_status", "check", "stage", "diagnosis"],
)
def test_table_covers_its_whole_enum(table, enum):
    # A status added to the enum without a tone would silently render muted.
    assert set(table) == {member.value for member in enum}
