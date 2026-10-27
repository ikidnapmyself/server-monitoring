"""Break-glass admin for notify models. Operators work in the console."""

from django.contrib import admin

from apps.notify.models import NotificationChannel


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    """Its ``config`` holds secrets, so only the console adds a channel or sets it."""

    list_display = ["name", "driver", "is_active", "updated_at"]
    exclude = ["config"]
    readonly_fields = ["driver"]

    def has_add_permission(self, request):
        return False
