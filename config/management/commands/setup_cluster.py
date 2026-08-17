"""Guided, self-verifying cluster setup: make this node a hub or an agent.

Usage:
    python manage.py setup_cluster                       # interactive
    python manage.py setup_cluster --role hub [--name "agent web-03"] \\
        [--notify-driver slack --notify-webhook <url> | --no-notify]
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
        parser.add_argument(
            "--notify-driver",
            dest="notify_driver",
            choices=["slack", "generic"],
            help="Hub: notification channel driver to create (email: add via admin).",
        )
        parser.add_argument(
            "--notify-webhook",
            dest="notify_webhook",
            help="Hub: webhook URL for the notification channel.",
        )
        parser.add_argument(
            "--no-notify",
            action="store_true",
            dest="no_notify",
            help="Hub: skip notification-channel setup.",
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

        self._ensure_notification_channel(options)

        active = APIKey.objects.filter(is_active=True).count()
        self.stdout.write("")
        self.stdout.write(f"Accepting pushes: yes ({active} active key(s), auth on).")
        self.stdout.write("If the hub web process was already running with auth off, restart it.")

    def _ensure_notification_channel(self, options) -> None:
        """Wire one notification channel so the hub actually notifies (or warn if not)."""
        from apps.notify.models import NotificationChannel
        from apps.notify.views import DRIVER_REGISTRY

        existing = NotificationChannel.objects.filter(is_active=True).first()
        if existing:
            self._bind_catchall_pipeline(existing)
            self.stdout.write(
                f"Notifications: active ({existing.driver}: {existing.name}), "
                "routed via the catch-all pipeline."
            )
            return

        driver = options.get("notify_driver")
        if not driver and not options.get("no_notify"):
            choice = (
                input("Set up a notification channel now? [slack/generic/skip]: ").strip().lower()
            )
            if choice in ("slack", "generic"):
                driver = choice

        if not driver:
            self.stdout.write(
                self.style.WARNING(
                    "Notifications: NONE — the hub accepts pushes but will not send anything. "
                    "Add one in Django admin (Notify → Notification channels) or re-run with "
                    "--notify-driver slack --notify-webhook <url>."
                )
            )
            return

        webhook = options.get("notify_webhook") or input(f"{driver} webhook URL: ").strip()
        config = {"webhook_url": webhook}
        driver_cls = DRIVER_REGISTRY.get(driver)
        # DRIVER_REGISTRY only holds concrete driver classes; mypy sees the abstract base.
        if driver_cls is None or not driver_cls().validate_config(config):  # type: ignore[abstract]
            raise CommandError(
                f"Invalid {driver} webhook. Slack needs an https://hooks.slack.com/... URL; "
                "generic needs an https:// endpoint."
            )

        channel = NotificationChannel.objects.create(
            name=f"{driver}-primary", driver=driver, config=config, is_active=True
        )
        self._bind_catchall_pipeline(channel)
        self.stdout.write(
            self.style.SUCCESS(
                f"Notifications: {driver} channel active, routed via the catch-all pipeline."
            )
        )

    def _bind_catchall_pipeline(self, channel) -> None:
        """Route the channel through the catch-all lane that actually wins.

        Wires the channel via routing instead of relying on "first active channel",
        so it composes with the pipeline-routing spine. Idempotent: the lane is
        repaired to the catch-all invariants (active, empty match, notify on) so the
        "routed via the catch-all pipeline" claim holds.

        The lane is selected the way ``resolve_pipeline`` selects one — first active
        empty-match lane by ``(priority, id)`` — rather than by a name this command
        owns. Binding a lane by name is how this silently broke: migration 0012
        seeds a ``catch-all`` at priority 1000 and ``migrate`` runs before
        ``setup_cluster``, so a ``default-catch-all`` created here tied on priority,
        lost the tie on ``id``, and never ran. Delivery then fell through to
        ``NotifySelector``'s "first active channel by name" — right by luck on a
        single-channel install, wrong on any other — while this command printed
        "routed via the catch-all pipeline" about a lane that never fired.
        Selecting the winner is self-correcting: it also does the right thing when
        an operator has a catch-all of their own.
        """
        from apps.orchestration.models import PipelineDefinition, PipelineStage

        pipeline = (
            PipelineDefinition.objects.filter(is_active=True, match=[])
            .order_by("priority", "id")
            .first()
        )
        created = False
        if pipeline is None:
            # Nothing routes catch-all traffic today. Fall back to the lane this
            # command owns — get_or_create, not create, because an inactive or
            # mis-matched ``default-catch-all`` from an earlier run is invisible to
            # the query above and a bare create() would trip the unique name. The
            # repair block below then puts it back into catch-all shape.
            pipeline, created = PipelineDefinition.objects.get_or_create(
                name="default-catch-all",
                defaults={
                    "match": [],
                    "priority": 1000,
                    "stages": list(PipelineDefinition.ROUTABLE_STAGES),
                },
            )
        notify = PipelineStage.NOTIFY.value
        # Read through routable_stages() rather than the raw column: clean() only runs
        # on admin forms, so a fixture or shell edit can persist junk. A bare string
        # would substring-match ("notify" in "notify") and skip the repair, and
        # unhashable junk would make the set() below raise TypeError instead of being
        # normalised away.
        current_stages = pipeline.routable_stages()
        if not created and not (
            pipeline.is_active and pipeline.match == [] and notify in current_stages
        ):
            pipeline.is_active = True
            pipeline.match = []
            # Keep whatever stages the operator selected, but guarantee NOTIFY,
            # inserted in canonical order.
            selected = set(current_stages) | {notify}
            pipeline.stages = [s for s in PipelineDefinition.ROUTABLE_STAGES if s in selected]
            pipeline.save(update_fields=["is_active", "match", "stages", "updated_at"])
        # A lane has exactly one channel. Claim the slot when it is empty or holds a
        # dead channel — otherwise the "routed via the catch-all pipeline" message
        # above would be false. An already-wired *active* channel is an operator
        # decision and is left alone.
        if pipeline.routed_channel() is None:
            pipeline.channel = channel
            pipeline.save(update_fields=["channel", "updated_at"])

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
