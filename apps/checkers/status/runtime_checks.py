"""Environment vs runtime state consistency checks.

Detects contradictions like DEBUG=True in production or Celery eager mode
in production.
"""

import os

from django.conf import settings

from apps.checkers.status import CheckResult

CATEGORY = "runtime"


def _is_production() -> bool:
    return os.environ.get("DJANGO_ENV", "dev") in ("prod", "production")


def run() -> list[CheckResult]:
    results: list[CheckResult] = []
    prod = _is_production()

    if prod:
        if settings.DEBUG:
            results.append(
                CheckResult(
                    level="error",
                    message="DEBUG is enabled in production",
                    hint="Set DJANGO_DEBUG=0 in .env for production.",
                    category=CATEGORY,
                )
            )

        if not settings.ALLOWED_HOSTS:
            results.append(
                CheckResult(
                    level="error",
                    message="ALLOWED_HOSTS is empty in production",
                    hint="Set DJANGO_ALLOWED_HOSTS in .env.",
                    category=CATEGORY,
                )
            )

        if settings.CELERY_TASK_ALWAYS_EAGER:
            results.append(
                CheckResult(
                    level="warn",
                    message="Celery is in eager mode in production",
                    hint="Set CELERY_TASK_ALWAYS_EAGER=0 for real task execution.",
                    category=CATEGORY,
                )
            )

    backend = getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging")
    statsd_host = getattr(settings, "STATSD_HOST", "localhost")

    if backend == "logging" and statsd_host != "localhost":
        results.append(
            CheckResult(
                level="info",
                message=f"StatsD host is configured ({statsd_host}) but metrics backend is 'logging'",
                hint="Set ORCHESTRATION_METRICS_BACKEND=statsd to use StatsD.",
                category=CATEGORY,
            )
        )

    if backend == "statsd" and statsd_host == "localhost":
        results.append(
            CheckResult(
                level="warn",
                message="Metrics backend is 'statsd' but STATSD_HOST is still 'localhost'",
                hint="Set STATSD_HOST to your StatsD server address.",
                category=CATEGORY,
            )
        )

    if not results:
        results.append(
            CheckResult(
                level="ok",
                message="Runtime configuration is consistent",
                category=CATEGORY,
            )
        )

    return results
