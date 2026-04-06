"""System status dashboard data and rendering.

Produces structured data for the system profile, pipeline state,
and pipeline definitions. Used by the system_status command for
both human-readable and JSON output.
"""

import os

from django.conf import settings


def get_profile() -> dict:
    """Build a system profile dict from Django settings and environment."""
    hub_url = getattr(settings, "HUB_URL", "")
    cluster_enabled = getattr(settings, "CLUSTER_ENABLED", False)

    if hub_url and cluster_enabled:
        role = "conflict"
    elif hub_url:
        role = "agent"
    elif cluster_enabled:
        role = "hub"
    else:
        role = "standalone"

    db_config = settings.DATABASES.get("default", {})
    db_name = str(db_config.get("NAME", ""))

    return {
        "role": role,
        "hub_url": hub_url,
        "environment": os.environ.get("DJANGO_ENV", "dev"),
        "debug": settings.DEBUG,
        "deploy_method": os.environ.get("DEPLOY_METHOD", "bare"),
        "database": db_name,
        "celery_broker": getattr(settings, "CELERY_BROKER_URL", ""),
        "celery_eager": getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False),
        "metrics_backend": getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging"),
        "instance_id": getattr(settings, "INSTANCE_ID", ""),
        "logs_dir": str(getattr(settings, "LOGS_DIR", "")),
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
    """Return all pipeline definitions with rendered chains and stages."""
    from apps.orchestration.models import PipelineDefinition

    definitions = []
    for defn in PipelineDefinition.objects.order_by("-is_active", "name"):
        definitions.append(
            {
                "name": defn.name,
                "active": defn.is_active,
                "chain": render_definition_chain(defn),
                "stages": _extract_stages(defn),
            }
        )
    return definitions


def render_definition_chain(defn) -> str:
    """Render a pipeline definition's nodes as a human-readable chain string."""
    nodes = defn.get_nodes()
    if not nodes:
        return "(no stages)"

    parts = []
    for node in nodes:
        node_type = node.get("type", "unknown")
        config = node.get("config", {})

        detail_parts = []
        for key in ("driver", "provider"):
            if key in config:
                detail_parts.append(config[key])
        if "checkers" in config:
            detail_parts.append(",".join(config["checkers"]))
        if "drivers" in config:
            detail_parts.append(",".join(config["drivers"]))
        if "channels" in config:
            detail_parts.append(",".join(config["channels"]))

        if detail_parts:
            parts.append(f"{node_type}: {','.join(detail_parts)}")
        else:
            parts.append(node_type)

    return " \u2192 ".join(parts)


def _extract_stages(defn) -> list[dict]:
    """Extract stage metadata from a pipeline definition's nodes."""
    nodes = defn.get_nodes()
    stages = []
    for node in nodes:
        stage: dict = {"stage": node.get("type", "unknown")}
        config = node.get("config", {})
        for key in (
            "driver",
            "drivers",
            "provider",
            "providers",
            "checkers",
            "channels",
        ):
            if key in config:
                val = config[key]
                stage[key] = val if isinstance(val, list) else [val]
        stages.append(stage)
    return stages
