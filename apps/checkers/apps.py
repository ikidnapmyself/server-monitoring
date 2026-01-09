from django.apps import AppConfig


class CheckersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.checkers"

    def ready(self):
        # Import checks module to register system checks with Django
        from apps.checkers import checks  # noqa: F401
