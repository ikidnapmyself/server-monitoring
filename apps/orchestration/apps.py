"""Django app configuration for the orchestration app."""

from django.apps import AppConfig


class OrchestrationConfig(AppConfig):
    """Configuration for the Pipeline Orchestration app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orchestration"
    verbose_name = "Pipeline Orchestration"
