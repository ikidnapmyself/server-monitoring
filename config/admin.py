"""Custom admin site for the server monitoring ops console."""

from django.contrib import admin
from django.contrib.admin import AdminSite

from config.dashboard import get_dashboard_context


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring"
    site_title = "Server Monitoring"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"

    def index(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(get_dashboard_context())
        return super().index(request, extra_context=extra_context)


class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "masked_key", "is_active", "created_at", "last_used_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    readonly_fields = ["key", "created_at", "last_used_at"]

    @admin.display(description="Key")
    def masked_key(self, obj):
        return f"{obj.prefix}***"
