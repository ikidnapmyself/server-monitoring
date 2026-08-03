"""Unified read-only diagnostic: preflight checks + cluster/node readiness."""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.alerts.models import Node
from apps.checkers.preflight.checks import run_all
from apps.checkers.preflight.dashboard import get_profile
from config.models import APIKey

BASE_DIR = Path(settings.BASE_DIR)


def _cluster_status() -> dict:
    active_keys = APIKey.objects.filter(is_active=True).count()
    auth_enabled = bool(getattr(settings, "API_KEY_AUTH_ENABLED", False))
    return {
        # A node accepts pushes iff it has an active API key and auth is on.
        "accepting_pushes": active_keys > 0 and auth_enabled,
        "active_api_keys": active_keys,
        "api_key_auth_enabled": auth_enabled,
        "known_nodes": Node.objects.count(),
        "hub_url": getattr(settings, "HUB_URL", ""),
    }


def _inbox_status() -> dict:
    """Durable-ingest queue depth (backpressure signal for the process_inbox drain)."""
    from apps.orchestration.models import PipelineRun, PipelineStatus

    pending = PipelineRun.objects.filter(status=PipelineStatus.PENDING).count()
    processing = PipelineRun.objects.filter(status=PipelineStatus.PROCESSING).count()
    threshold = int(getattr(settings, "INBOX_DEPTH_WARN", 500))
    return {
        "pending": pending,
        "processing": processing,
        "warn_threshold": threshold,
        "over_threshold": pending > threshold,
    }


class Command(BaseCommand):
    help = "Read-only node/cluster diagnostic (preflight + registry + ingest readiness)."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        profile = get_profile()
        cluster = _cluster_status()
        inbox = _inbox_status()
        checks = [{"level": r.level, "message": r.message} for r in run_all(base_dir=BASE_DIR)]
        if inbox["over_threshold"]:
            checks.append(
                {
                    "level": "WARNING",
                    "message": (
                        f"Inbox backlog: {inbox['pending']} PENDING runs "
                        f"(> {inbox['warn_threshold']}). Is a process_inbox drain running?"
                    ),
                }
            )
        data = {"profile": profile, "cluster": cluster, "inbox": inbox, "checks": checks}

        if options["as_json"]:
            self.stdout.write(json.dumps(data, indent=2, default=str))
            return

        self.stdout.write(self.style.HTTP_INFO("=== Doctor ==="))
        self.stdout.write(f"Role (derived): {profile.get('role')}")
        self.stdout.write(
            f"Accepting pushes: {cluster['accepting_pushes']} "
            f"({cluster['active_api_keys']} active key(s), "
            f"auth={'on' if cluster['api_key_auth_enabled'] else 'off'})"
        )
        self.stdout.write(f"Known nodes: {cluster['known_nodes']}")
        self.stdout.write(f"Inbox: {inbox['pending']} pending, {inbox['processing']} processing")
        for c in checks:
            self.stdout.write(f"  [{c['level']}] {c['message']}")
