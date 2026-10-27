"""The tone tables: one tone per domain status."""

import pytest

from apps.alerts.models import AlertSeverity, AlertStatus, IncidentStatus
from apps.checkers.models import CheckStatus
from config.tones import (
    ALERT_STATUS_TONES,
    CHECK_STATUS_TONES,
    INCIDENT_STATUS_TONES,
    SEVERITY_TONES,
    TONES,
    tone_for,
)

_ALL_TABLES = {
    "severity": SEVERITY_TONES,
    "alert_status": ALERT_STATUS_TONES,
    "incident_status": INCIDENT_STATUS_TONES,
    "check": CHECK_STATUS_TONES,
}


def test_tones_are_the_five_documented_ones():
    assert TONES == frozenset({"critical", "warning", "info", "ok", "muted"})


def test_tone_for_maps_a_known_value():
    assert tone_for(INCIDENT_STATUS_TONES, "acknowledged") == "warning"


def test_skipped_check_renders_muted():
    assert CHECK_STATUS_TONES["skipped"] == "muted"


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
    ],
    ids=["severity", "alert_status", "incident_status", "check"],
)
def test_table_covers_its_whole_enum(table, enum):
    # A status added to the enum without a tone would silently render muted.
    assert set(table) == {member.value for member in enum}
