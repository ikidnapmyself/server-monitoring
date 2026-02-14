"""Custom admin site for the server monitoring ops console."""

from django.contrib.admin import AdminSite


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring"
    site_title = "Server Monitoring"
    index_title = "Dashboard"
    index_template = "admin/dashboard.html"
