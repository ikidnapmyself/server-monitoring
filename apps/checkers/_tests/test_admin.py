"""Tests for checkers admin (PreflightRun)."""

from django.contrib import admin

from apps.checkers.admin import PreflightCheckInline
from apps.checkers.models import PreflightCheck, PreflightRun


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


def test_preflight_check_inline_readonly():
    inline = PreflightCheckInline(PreflightCheck, admin.site)
    for field in ("level", "message", "hint"):
        assert field in inline.readonly_fields
    assert inline.extra == 0
    assert inline.has_add_permission(request=None) is False
    assert inline.has_change_permission(request=None) is False
    assert inline.has_delete_permission(request=None) is False
