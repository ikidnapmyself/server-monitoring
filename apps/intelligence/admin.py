"""Admin configuration for intelligence app."""

from django.contrib import admin

from apps.intelligence.models import AnalysisRun, IntelligenceProvider


@admin.register(IntelligenceProvider)
class IntelligenceProviderAdmin(admin.ModelAdmin):
    """Read-only. Operators edit providers in the console."""

    list_display = ["name", "provider", "is_active", "updated_at"]
    exclude = ["config"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    """Read-only. Operators read analyses in the console."""

    list_display = ["created_at", "provider", "status", "total_tokens"]
    exclude = ["provider_config"]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
