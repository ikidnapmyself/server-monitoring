"""The break-glass admin: stock Django, for active superusers only."""

from django.contrib import admin
from django.contrib.admin import AdminSite


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring break-glass admin"
    site_title = "Server Monitoring"
    index_title = "Raw rows. An edit here skips the console's checks."
    site_url = "/console/"

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser


class APIKeyAdmin(admin.ModelAdmin):
    """Read-only. Operators edit API keys in the console."""

    list_display = ["name", "masked_key", "is_active", "created_at", "last_used_at"]
    exclude = ["key"]

    @admin.display(description="Key")
    def masked_key(self, obj):
        return f"{obj.prefix}***"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
