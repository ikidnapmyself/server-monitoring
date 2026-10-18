"""Admin configuration for alerts models."""

from django.contrib import admin

from apps.alerts.models import (
    Alert,
    AlertHistory,
    Incident,
    Node,
)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """Read-only. Operators read alerts in the console, and nobody resolves one by hand."""

    list_display = ["name", "severity", "status", "source", "node", "incident", "received_at"]
    list_filter = ["status", "severity", "source"]
    search_fields = ["name", "fingerprint", "trace_id"]
    list_select_related = ["node", "incident"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    """Read-only. Operators read and act on incidents in the console."""

    list_display = ["subject", "instance", "severity", "status", "created_at", "resolved_at"]
    list_filter = ["status", "severity"]
    search_fields = ["subject", "instance", "summary"]
    list_select_related = ["node", "pipeline"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    """Read-only audit rows, written by the code that changes an alert."""

    list_display = ["alert", "event", "old_status", "new_status", "created_at"]
    list_filter = ["event"]
    search_fields = ["alert__name"]
    list_select_related = ["alert"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    """Read-only. Operators read nodes and edit their policy in the console."""

    list_display = ["instance_id", "hostname", "last_source", "first_seen", "last_seen"]
    search_fields = ["instance_id", "hostname"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
