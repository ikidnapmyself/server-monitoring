"""Custom Django admin and config app configuration."""

from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class MonitoringAdminConfig(AdminConfig):
    default_site = "config.admin.MonitoringAdminSite"


class ConfigAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "config"
    label = "config_app"
    verbose_name = "Config"
