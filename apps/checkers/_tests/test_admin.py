"""Tests for checkers admin (PreflightRun)."""

import pytest
from django.contrib import admin

from apps.checkers.admin import CheckRunAdmin, PreflightCheckInline
from apps.checkers.models import CheckRun, PreflightCheck, PreflightRun


def test_preflight_run_registered():
    assert PreflightRun in admin.site._registry


def test_preflight_run_list_display():
    model_admin = admin.site._registry[PreflightRun]
    for field in ("overall_status", "passed", "warnings", "errors", "created_at"):
        assert field in model_admin.list_display


def test_preflight_run_date_hierarchy():
    model_admin = admin.site._registry[PreflightRun]
    assert model_admin.date_hierarchy == "created_at"


def test_preflight_run_has_inline():
    model_admin = admin.site._registry[PreflightRun]
    assert PreflightCheckInline in model_admin.inlines


def test_preflight_run_no_add_permission():
    model_admin = admin.site._registry[PreflightRun]
    assert model_admin.has_add_permission(request=None) is False


def test_preflight_run_no_change_or_delete_permission():
    # Audit history: view-only, no edits or deletion (matches CheckRunAdmin intent).
    model_admin = admin.site._registry[PreflightRun]
    assert model_admin.has_change_permission(request=None) is False
    assert model_admin.has_delete_permission(request=None) is False


def test_preflight_check_inline_readonly():
    inline = PreflightCheckInline(PreflightCheck, admin.site)
    for field in ("level", "message", "hint"):
        assert field in inline.readonly_fields
    assert inline.extra == 0
    assert inline.has_add_permission(request=None) is False
    assert inline.has_change_permission(request=None) is False
    assert inline.has_delete_permission(request=None) is False


@pytest.mark.parametrize(
    ("status", "tone"),
    [
        ("ok", "ok"),
        ("warning", "warning"),
        ("critical", "critical"),
        ("unknown", "muted"),
        ("something-new", "muted"),
    ],
)
def test_the_check_status_badge_carries_its_tone_class(status, tone):
    model_admin = CheckRunAdmin(CheckRun, admin.site)
    html = str(model_admin.status_badge(CheckRun(status=status)))
    assert f"ops-badge--{tone}" in html
    assert status.upper() in html


def test_the_check_status_badge_hardcodes_no_colour():
    model_admin = CheckRunAdmin(CheckRun, admin.site)
    for status in ("ok", "warning", "critical", "unknown"):
        assert "#" not in str(model_admin.status_badge(CheckRun(status=status)))
