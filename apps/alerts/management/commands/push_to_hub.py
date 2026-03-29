"""
Management command to push local checker results to a hub instance.

Usage:
    python manage.py push_to_hub                    # Run checkers, push to hub
    python manage.py push_to_hub --dry-run          # Show payload, don't POST
    python manage.py push_to_hub --json             # JSON output
    python manage.py push_to_hub --checkers cpu,memory  # Specific checkers only
"""

import hashlib
import hmac
import json
import socket
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.checkers.checkers import CHECKER_REGISTRY
from apps.checkers.checkers.base import CheckStatus


class Command(BaseCommand):
    help = "Run health checks and push results to a hub instance"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show payload without sending to hub.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output result as JSON.",
        )
        parser.add_argument(
            "--checkers",
            type=str,
            help="Comma-separated list of checkers to run (default: all).",
        )

    def handle(self, *args, **options):
        hub_url = getattr(settings, "HUB_URL", "")
        if not hub_url:
            raise CommandError("HUB_URL is not configured. Set it in .env to enable agent mode.")

        instance_id = getattr(settings, "INSTANCE_ID", "") or socket.gethostname()
        hostname = socket.gethostname()
        secret = getattr(settings, "WEBHOOK_SECRET_CLUSTER", "")

        # Determine which checkers to run
        checker_names = None
        if options.get("checkers"):
            checker_names = [c.strip() for c in options["checkers"].split(",")]

        # Run checkers
        alerts = []
        for name, checker_cls in CHECKER_REGISTRY.items():
            if checker_names and name not in checker_names:
                continue
            try:
                checker = checker_cls()
                result = checker.run()
                alert = self._result_to_alert(result, instance_id, hostname)
                alerts.append(alert)
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"Checker {name} failed: {e}"))

        # Build payload
        payload = {
            "source": "cluster",
            "instance_id": instance_id,
            "hostname": hostname,
            "version": "1.0",
            "alerts": alerts,
        }

        if options["dry_run"]:
            if options["json_output"]:
                self.stdout.write(json.dumps(payload, indent=2, default=str))
            else:
                self.stdout.write(self.style.NOTICE("Dry run — payload:"))
                self.stdout.write(json.dumps(payload, indent=2, default=str))
            return

        if not options["json_output"]:
            self.stdout.write(f"Pushing {len(alerts)} alert(s) from {instance_id} to {hub_url}")

        # POST to hub
        url = hub_url.rstrip("/") + "/alerts/webhook/cluster/"
        body = json.dumps(payload, default=str).encode()

        headers = {"Content-Type": "application/json"}
        if secret:
            signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Cluster-Signature"] = signature

        request = Request(url, data=body, headers=headers, method="POST")

        try:
            with urlopen(request, timeout=30) as response:
                status = response.status
                resp_body = response.read().decode()

            if status in (200, 201, 202):
                if options["json_output"]:
                    self.stdout.write(json.dumps(payload, indent=2, default=str))
                else:
                    self.stdout.write(self.style.SUCCESS(f"Hub accepted: HTTP {status}"))
            else:
                raise CommandError(f"Hub returned HTTP {status}: {resp_body}")

        except Exception as e:
            if isinstance(e, CommandError):
                raise
            raise CommandError(f"Failed to reach hub at {url}: {e}")

    def _result_to_alert(self, result, instance_id: str, hostname: str) -> dict:
        """Convert a CheckResult to a cluster alert dict."""
        # Map check status to alert status/severity
        if result.status == CheckStatus.OK:
            status = "resolved"
            severity = "info"
        elif result.status == CheckStatus.WARNING:
            status = "firing"
            severity = "warning"
        elif result.status == CheckStatus.CRITICAL:
            status = "firing"
            severity = "critical"
        else:
            status = "firing"
            severity = "warning"

        now = datetime.now(timezone.utc).isoformat()

        return {
            "fingerprint": f"{result.checker_name}-{hostname}",
            "name": f"{result.checker_name}: {result.message}",
            "status": status,
            "severity": severity,
            "started_at": now,
            "ended_at": now if status == "resolved" else None,
            "description": result.message,
            "labels": {
                "checker": result.checker_name,
                "hostname": hostname,
                "instance_id": instance_id,
            },
            "annotations": {
                "message": result.message,
            },
            "metrics": result.metrics,
        }
