"""Custom admin site for the server monitoring ops console."""

from django.contrib import admin, messages
from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.text import slugify

from apps.alerts.models import Node
from apps.alerts.policy_overview import build_policy_overview
from apps.orchestration.models import PipelineDefinition
from config.dashboard import get_dashboard_context
from config.netmap import get_map_context

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

# The two pages this site adds that are not model changelists. Each sits in the
# section holding the model it reports on, and is gated by that model's view
# permission so the link and the view agree about who may see it. Rendered by
# the sidebar and the dashboard's Navigate card, both of which read app_list.
SECTION_LINKS = {
    "Operations": [("Hub-side policy", "admin:policy-overview", Node)],
    "Configuration": [("Network map", "admin:netmap", PipelineDefinition)],
}


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring"
    site_title = "Server Monitoring"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"

    def get_urls(self):
        custom = [
            path("map/", self.admin_view(self.map_view), name="netmap"),
            path("policy/", self.admin_view(self.policy_view), name="policy-overview"),
        ]
        return custom + super().get_urls()

    def _section_links(self, request, name):
        """This section's non-model pages, as entries the app_list templates accept.

        ``view_only`` and the absent ``add_url`` are what stop the sidebar
        offering an add button for a page that has nothing to add. ``is_link``
        is read by nothing in Django; it is what lets a caller tell a page from
        a model without matching on the label.
        """
        entries = []
        for label, route, model in SECTION_LINKS.get(name, []):
            if not self._registry[model].has_view_permission(request):
                continue
            entries.append(
                {
                    "name": label,
                    "object_name": slugify(label),
                    "perms": {"view": True},
                    "admin_url": reverse(route, current_app=self.name),
                    "add_url": None,
                    "view_only": True,
                    "is_link": True,
                }
            )
        return entries

    def map_view(self, request):
        """The routing lanes, drawn. Gated on the model it draws.

        admin_view only asks for staff, and every card on this page is a
        PipelineDefinition, so the sidebar link and the page have to agree about
        who may look. Same reasoning as policy_view below.
        """
        if not self._registry[PipelineDefinition].has_view_permission(request):
            raise PermissionDenied
        context = {**self.each_context(request), **get_map_context(), "title": "Network map"}
        return render(request, "admin/map.html", context)

    def policy_view(self, request):
        """Every node's hub-side policy in one table."""
        # admin_view only asks for staff. This page prints the same facts a node page
        # shows, so NodeAdmin decides, which keeps view-or-change parity with it.
        if not self._registry[Node].has_view_permission(request):
            raise PermissionDenied
        context = {
            **self.each_context(request),
            "overview": build_policy_overview(),
            "title": "Hub-side policy",
        }
        return render(request, "admin/policy_overview.html", context)

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
            models += self._section_links(request, name)
            if models:
                sections.append(
                    {
                        "name": name,
                        "app_label": slugify(name),
                        # Header links to the first model's changelist — the synthetic section
                        # slug isn't a real app_label, so admin:app_list would 404.
                        "app_url": next((m["admin_url"] for m in models if m["admin_url"]), ""),
                        # Safe: models here are already permission-filtered by super(), and
                        # empty sections are dropped above.
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
                    # Header links to the first model's changelist — the synthetic section
                    # slug isn't a real app_label, so admin:app_list would 404.
                    "app_url": next((m["admin_url"] for m in leftover if m["admin_url"]), ""),
                    # Safe: models here are already permission-filtered by super(), and
                    # empty sections are dropped above.
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
