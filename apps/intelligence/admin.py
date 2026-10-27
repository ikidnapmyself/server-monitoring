"""Break-glass admin for intelligence models. Operators work in the console."""

from django.contrib import admin

from apps.intelligence.models import AnalysisRun, IntelligenceProvider
from config.admin import HidesSecretsAdmin


@admin.register(IntelligenceProvider)
class IntelligenceProviderAdmin(HidesSecretsAdmin):
    """Its ``config`` holds secrets, so only the console adds a provider or sets it."""

    list_display = ["name", "provider", "is_active", "updated_at"]
    exclude = ["config"]
    readonly_fields = ["provider"]

    def has_add_permission(self, request):
        return False


@admin.register(AnalysisRun)
class AnalysisRunAdmin(HidesSecretsAdmin):
    list_display = ["created_at", "provider", "status", "total_tokens"]
    exclude = ["provider_config"]
    raw_id_fields = ["incident"]
