"""Admin configuration for notify models."""

from django.contrib import admin

from apps.notify.models import NotificationChannel


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    """Read-only. Operators edit channels in the console."""

    list_display = ["name", "driver", "is_active", "updated_at"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
