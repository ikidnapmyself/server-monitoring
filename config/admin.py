"""Custom admin site for the server monitoring ops console."""

from django.contrib import admin, messages
from django.contrib.admin import AdminSite
from django.utils.text import slugify

from config.dashboard import get_dashboard_context

SECTION_MAP = {
    "Operations": [
        "alerts.incident",
        "alerts.alert",
        "orchestration.inboxitem",
        "orchestration.pipelinerun",
        "alerts.node",
    ],
    "Configuration": [
        "notify.notificationchannel",
        "intelligence.intelligenceprovider",
        "orchestration.pipelinedefinition",
        "config_app.apikey",
        "auth.user",
        "auth.group",
    ],
    "History & Audit": [
        "checkers.checkrun",
        "checkers.preflightrun",
        "intelligence.analysisrun",
        "alerts.alerthistory",
        "orchestration.stageexecution",
    ],
}


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring"
    site_title = "Server Monitoring"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"

    def index(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(get_dashboard_context())
        return super().index(request, extra_context=extra_context)

    def get_app_list(self, request, app_label=None):
        if app_label is not None:
            return super().get_app_list(request, app_label)
        default = super().get_app_list(request, app_label)
        by_key = {}
        for app in default:
            for model in app["models"]:
                by_key[f"{app['app_label']}.{model['object_name'].lower()}"] = model
        sections, used = [], set()
        for name, keys in SECTION_MAP.items():
            models = [by_key[k] for k in keys if k in by_key]
            used.update(k for k in keys if k in by_key)
            if models:
                sections.append(
                    {
                        "name": name,
                        "app_label": slugify(name),
                        "app_url": models[0]["admin_url"],
                        "has_module_perms": True,
                        "models": models,
                    }
                )
        leftover = [m for k, m in by_key.items() if k not in used]
        if leftover:
            sections.append(
                {
                    "name": "Other",
                    "app_label": "other",
                    "app_url": leftover[0]["admin_url"],
                    "has_module_perms": True,
                    "models": leftover,
                }
            )
        return sections


class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "masked_key", "is_active", "created_at", "last_used_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    readonly_fields = ["key", "created_at", "last_used_at"]

    @admin.display(description="Key")
    def masked_key(self, obj):
        return f"{obj.prefix}***"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        raw = getattr(obj, "_raw_key", "")
        if not change and raw:
            messages.warning(
                request,
                f"Raw token for '{obj.name}' (shown once — copy it now): {raw}",
            )
