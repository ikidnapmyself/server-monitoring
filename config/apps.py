"""Custom Django admin app configuration."""

from django.contrib.admin.apps import AdminConfig


class MonitoringAdminConfig(AdminConfig):
    default_site = "config.admin.MonitoringAdminSite"
