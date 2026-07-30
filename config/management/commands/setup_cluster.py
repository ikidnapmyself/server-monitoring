"""Guided, self-verifying cluster setup: make this node a hub or an agent.

Usage:
    python manage.py setup_cluster                       # interactive
    python manage.py setup_cluster --role hub [--name "agent web-03"]
    python manage.py setup_cluster --role agent \\
        --hub-url https://hub.example.com --instance-id web-03 --hub-api-key <token>
    python manage.py setup_cluster --role agent ... --no-verify
"""

import socket
from pathlib import Path
from urllib.error import HTTPError

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.alerts.management.commands.push_to_hub import build_cluster_payload, send_to_hub
from config.models import APIKey
from config.security.url_validation import URLNotAllowedError


def env_upsert(path: Path, key: str, value: str) -> None:
    """Idempotently set ``key=value`` in a .env file (replace the line or append)."""
    lines = path.read_text().splitlines() if path.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def _env_path() -> Path:
    return Path(settings.BASE_DIR) / ".env"


def explain_http_error(code: int) -> str:
    """Turn a hub HTTP failure into a named, fixable message."""
    if code == 401:
        return "Hub rejected the key (401). Check HUB_API_KEY matches a token minted on the hub."
    if code == 403:
        return (
            "Hub returned 403. Either a WAF blocks the agent User-Agent "
            "(allowlist 'server-monitoring-agent' or the agent IP), or the key's "
            "allowed_endpoints excludes /alerts/webhook/cluster/ (widen the key's scope)."
        )
    return f"Hub returned HTTP {code} (expected 202)."


class Command(BaseCommand):
    help = "Guided, self-verifying cluster setup (hub or agent)."

    def add_arguments(self, parser):
        parser.add_argument("--role", choices=["hub", "agent"])
        parser.add_argument("--name", help="Label for the hub API key.")
        parser.add_argument("--hub-url", dest="hub_url")
        parser.add_argument("--instance-id", dest="instance_id")
        parser.add_argument("--hub-api-key", dest="hub_api_key")
        parser.add_argument(
            "--no-verify",
            action="store_true",
            dest="no_verify",
            help="Agent: skip the live push verification.",
        )

    def handle(self, *args, **options):
        role = options.get("role") or self._prompt_role()
        if role == "hub":
            self._setup_hub(options)
        else:
            self._setup_agent(options)

    def _prompt_role(self) -> str:
        self.stdout.write("Set up this node as:")
        self.stdout.write("  1) hub   — accept pushes from other nodes")
        self.stdout.write("  2) agent — push this node's checks to a hub")
        while True:
            choice = input("Select [1/2]: ").strip()
            if choice == "1":
                return "hub"
            if choice == "2":
                return "agent"

    def _setup_hub(self, options) -> None:
        env_upsert(_env_path(), "API_KEY_AUTH_ENABLED", "1")
        name = options.get("name") or input('API key label (e.g. "agent web-03"): ').strip()
        if not name:
            name = "cluster-agent"
        api_key = APIKey.objects.create(name=name)
        raw = getattr(api_key, "_raw_key", "")

        self.stdout.write(
            self.style.SUCCESS(f"Hub ready — API_KEY_AUTH_ENABLED=1, key '{name}' minted.")
        )
        self.stdout.write("")
        self.stdout.write("Raw token (shown once — set it as HUB_API_KEY on the agent):")
        self.stdout.write(f"    {raw}")
        self.stdout.write("")
        active = APIKey.objects.filter(is_active=True).count()
        self.stdout.write(f"Accepting pushes: yes ({active} active key(s), auth on).")
        self.stdout.write("If the hub web process was already running with auth off, restart it.")

    def _setup_agent(self, options) -> None:
        hub_url = options.get("hub_url") or input("HUB_URL (https://...): ").strip()
        default_id = socket.gethostname()
        instance_id = options.get("instance_id")
        if not instance_id:
            instance_id = input(f"INSTANCE_ID [{default_id}]: ").strip() or default_id
        api_key = options.get("hub_api_key") or input("HUB_API_KEY (token from the hub): ").strip()

        if not hub_url or not api_key:
            raise CommandError("HUB_URL and HUB_API_KEY are required for agent setup.")

        env_upsert(_env_path(), "HUB_URL", hub_url)
        env_upsert(_env_path(), "INSTANCE_ID", instance_id)
        env_upsert(_env_path(), "HUB_API_KEY", api_key)
        self.stdout.write(self.style.SUCCESS("Agent config written to .env."))

        if options.get("no_verify"):
            self.stdout.write("Skipping verification (--no-verify).")
            return

        self.stdout.write("Verifying with a live push...")
        payload = build_cluster_payload(instance_id, default_id, [])
        try:
            status, _ = send_to_hub(hub_url, api_key, payload)
        except URLNotAllowedError:
            raise CommandError(
                "Hub URL blocked by SSRF policy — check HUB_URL / SSRF_ALLOWED_HOSTS."
            )
        except ValueError as e:
            raise CommandError(str(e))
        except HTTPError as e:
            raise CommandError(explain_http_error(e.code))
        except Exception as e:
            raise CommandError(f"Could not reach the hub at {hub_url}: {e}")

        if status in (200, 201, 202):
            self.stdout.write(
                self.style.SUCCESS(
                    f"Verified — hub accepted the push (HTTP {status}). Agent is ready."
                )
            )
        else:
            raise CommandError(f"Hub returned HTTP {status} (expected 202).")
