"""Admin configuration for notify models."""

from django.contrib import admin
from django.db import models as db_models
from django_json_widget.widgets import JSONEditorWidget

from apps.notify.models import NotificationChannel


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    """Admin for NotificationChannel model."""

    list_display = [
        "name",
        "driver",
        "is_active",
        "created_at",
        "updated_at",
    ]
    formfield_overrides = {db_models.JSONField: {"widget": JSONEditorWidget}}
    list_filter = ["driver", "is_active"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = [
        (
            None,
            {
                "fields": ["name", "driver", "is_active", "description"],
            },
        ),
        (
            "Configuration",
            {
                "fields": ["config"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]
