"""Admin configuration for checkers models."""

from django.contrib import admin

from apps.checkers.models import CheckRun, PreflightRun


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    """Read-only. Operators read check runs in the console."""

    list_display = ["checker_name", "hostname", "status", "executed_at"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PreflightRun)
class PreflightRunAdmin(admin.ModelAdmin):
    """Read-only. Operators read preflight runs in the console."""

    list_display = ["created_at", "overall_status", "passed", "warnings", "errors"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
