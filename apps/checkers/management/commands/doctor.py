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


class Command(BaseCommand):
    help = "Read-only node/cluster diagnostic (preflight + registry + ingest readiness)."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        profile = get_profile()
        cluster = _cluster_status()
        checks = [{"level": r.level, "message": r.message} for r in run_all(base_dir=BASE_DIR)]
        data = {"profile": profile, "cluster": cluster, "checks": checks}

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
        for c in checks:
            self.stdout.write(f"  [{c['level']}] {c['message']}")
