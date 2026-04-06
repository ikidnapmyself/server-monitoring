"""Database vs config state consistency checks.

Compares database records (pipeline definitions, notification channels,
intelligence providers) against environment configuration.
"""

from django.conf import settings

from apps.checkers.status import CheckResult

CATEGORY = "database"


def run() -> list[CheckResult]:
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.models import PipelineDefinition

    results: list[CheckResult] = []

    active_definitions = PipelineDefinition.objects.filter(is_active=True).count()
    active_channels = NotificationChannel.objects.filter(is_active=True).count()
    active_providers = IntelligenceProvider.objects.filter(is_active=True)

    if active_definitions > 0 and settings.CELERY_TASK_ALWAYS_EAGER:
        results.append(
            CheckResult(
                level="warn",
                message=(
                    f"{active_definitions} active pipeline definition(s) "
                    f"but Celery is in eager mode"
                ),
                hint=(
                    "Eager mode runs tasks inline — "
                    "set CELERY_TASK_ALWAYS_EAGER=0 for async execution."
                ),
                category=CATEGORY,
            )
        )

    if active_channels == 0:
        results.append(
            CheckResult(
                level="warn",
                message="No active notification channels configured",
                hint="Add a notification channel via Django Admin.",
                category=CATEGORY,
            )
        )

    if active_definitions == 0:
        results.append(
            CheckResult(
                level="info",
                message="No active pipeline definitions",
                hint="Create one via Django Admin or run: manage.py setup_instance",
                category=CATEGORY,
            )
        )

    fallback = getattr(settings, "ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED", True)
    if active_providers.exists() and not fallback:
        results.append(
            CheckResult(
                level="info",
                message="Intelligence provider is active but fallback is disabled",
                hint=(
                    "If the AI provider fails, the pipeline will fail. "
                    "Set ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=1 "
                    "to continue on failure."
                ),
                category=CATEGORY,
            )
        )

    if not results:
        results.append(
            CheckResult(
                level="ok",
                message="Database state is consistent with config",
                category=CATEGORY,
            )
        )

    return results
