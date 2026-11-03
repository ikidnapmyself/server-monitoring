"""The config app's configuration."""

from django.apps import AppConfig


class ConfigAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "config"
    label = "config_app"
    verbose_name = "Config"

    def ready(self):
        import config.checks  # noqa: F401
