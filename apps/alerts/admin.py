"""Break-glass admin for alerts models. Operators work in the console."""

from django.contrib import admin

from apps.alerts.models import Alert, AlertHistory, Incident, Node


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ["name", "severity", "status", "source", "node", "incident", "received_at"]
    list_filter = ["status", "severity", "source"]
    search_fields = ["name", "fingerprint", "trace_id"]
    list_select_related = ["node", "incident"]
    raw_id_fields = ["node", "incident"]


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ["subject", "instance", "severity", "status", "created_at", "resolved_at"]
    list_filter = ["status", "severity"]
    search_fields = ["subject", "instance", "summary"]
    list_select_related = ["node", "pipeline"]
    raw_id_fields = ["node"]
    date_hierarchy = "created_at"


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    list_display = ["alert", "event", "old_status", "new_status", "created_at"]
    list_filter = ["event"]
    search_fields = ["alert__name"]
    list_select_related = ["alert"]
    raw_id_fields = ["alert"]


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ["instance_id", "hostname", "last_source", "first_seen", "last_seen"]
    search_fields = ["instance_id", "hostname"]
