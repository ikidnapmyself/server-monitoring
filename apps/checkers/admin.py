"""Break-glass admin for checkers models. Operators work in the console."""

from django.contrib import admin

from apps.checkers.models import CheckRun, PreflightRun


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    list_display = ["checker_name", "hostname", "status", "executed_at"]
    raw_id_fields = ["alert"]


@admin.register(PreflightRun)
class PreflightRunAdmin(admin.ModelAdmin):
    list_display = ["created_at", "overall_status", "passed", "warnings", "errors"]
