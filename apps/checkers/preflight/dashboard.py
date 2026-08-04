"""System status dashboard data and rendering.

Produces structured data for the system profile, pipeline state,
and pipeline definitions. Used by the system_status command for
both human-readable and JSON output.
"""

import os

from django.conf import settings


def _is_receiving() -> bool:
    """A node accepts pushes (is a hub) when auth is on and it has an active key."""
    from config.models import APIKey

    # Default True to match the API-key middleware and get_profile()'s api_key_auth
    # field, so role/receiving never disagrees with the actual auth gate.
    if not getattr(settings, "API_KEY_AUTH_ENABLED", True):
        return False
    return APIKey.objects.filter(is_active=True).exists()


def get_profile() -> dict:
    """Build a system profile dict from Django settings and the API-key state."""
    hub_url = getattr(settings, "HUB_URL", "")
    is_agent = bool(hub_url)
    receiving = _is_receiving()

    if is_agent and receiving:
        role = "agent+hub"
    elif is_agent:
        role = "agent"
    elif receiving:
        role = "hub"
    else:
        role = "standalone"

    db_config = settings.DATABASES.get("default", {})
    db_name = str(db_config.get("NAME", ""))

    return {
        "role": role,
        "receiving": receiving,
        "hub_url": hub_url,
        "environment": os.environ.get("DJANGO_ENV", "dev"),
        "debug": settings.DEBUG,
        "deploy_method": os.environ.get("DEPLOY_METHOD", "bare"),
        "database": db_name,
        "inbox_depth_warn": getattr(settings, "INBOX_DEPTH_WARN", 500),
        "metrics_backend": getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging"),
        "instance_id": getattr(settings, "INSTANCE_ID", ""),
        "logs_dir": str(getattr(settings, "LOGS_DIR", "")),
        "api_key_auth": getattr(settings, "API_KEY_AUTH_ENABLED", True),
        "rate_limiting": getattr(settings, "RATE_LIMIT_ENABLED", False),
    }


def get_pipeline_state() -> dict:
    """Return current pipeline state: channels, intelligence providers, last run."""
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.models import PipelineRun

    channels = list(
        NotificationChannel.objects.all().order_by("name").values("name", "driver", "is_active")
    )
    intelligence = list(
        IntelligenceProvider.objects.filter(is_active=True)
        .order_by("name")
        .values("name", "provider", "is_active")
    )

    last_run_qs = PipelineRun.objects.order_by("-created_at").first()
    last_run = None
    if last_run_qs:
        last_run = {
            "timestamp": (last_run_qs.created_at.isoformat() if last_run_qs.created_at else None),
            "status": last_run_qs.status,
            "run_id": last_run_qs.run_id,
        }

    return {
        "channels": channels,
        "intelligence": intelligence,
        "last_run": last_run,
    }


def get_definitions() -> list[dict]:
    """Return routing pipelines (name, active, priority, channel count)."""
    from apps.orchestration.models import PipelineDefinition

    return [
        {
            "name": defn.name,
            "active": defn.is_active,
            "priority": defn.priority,
            "channels": defn.channels.count(),
        }
        for defn in PipelineDefinition.objects.order_by("priority", "name")
    ]
