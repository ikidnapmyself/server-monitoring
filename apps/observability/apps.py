from django.apps import AppConfig


class ObservabilityConfig(AppConfig):
    name = "apps.observability"
    label = "observability"
    verbose_name = "Observability"

    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from apps.observability import checks  # noqa: F401  (register side-effect)
