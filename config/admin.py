"""Custom admin site for the server monitoring ops console."""

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
