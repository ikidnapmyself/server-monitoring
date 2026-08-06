"""
Management command to push local checker results to a hub instance.

Usage:
    python manage.py push_to_hub                    # Run checkers, push to hub
    python manage.py push_to_hub --dry-run          # Show payload, don't POST
    python manage.py push_to_hub --json             # JSON output
    python manage.py push_to_hub --checkers cpu,memory  # Specific checkers only
"""

import json
import socket
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request  # noqa: TID251 — Request is a data object, not urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.checkers.checkers import CHECKER_REGISTRY
from apps.checkers.checkers.base import CheckStatus
from config.security.http import safe_urlopen
from config.security.url_validation import URLNotAllowedError

CLUSTER_WEBHOOK_PATH = "/alerts/webhook/cluster/"
# Identify the agent explicitly. urllib's default UA is "Python-urllib/<ver>",
# which WAFs in front of a hub routinely block with a 403; an identifiable UA is
# also what an operator allowlists.
AGENT_USER_AGENT = "server-monitoring-agent/1.0"


def build_cluster_payload(instance_id: str, hostname: str, alerts: list) -> dict:
    """Build the cluster webhook payload from checker-derived alerts."""
    return {
        "source": "cluster",
        "instance_id": instance_id,
        "hostname": hostname,
        "version": "1.0",
        "alerts": alerts,
    }


def send_to_hub(hub_url: str, api_key: str, payload: dict) -> tuple[int, str]:
    """POST a cluster payload to a hub's webhook; return (status, body).

    Assumes ``hub_url`` and ``api_key`` are non-empty. Raises ``ValueError`` on a
    bad scheme, ``URLNotAllowedError`` on SSRF policy, and lets transport errors
    (``HTTPError`` for 4xx/5xx, ``URLError``) propagate so callers can inspect them.
    Shared by ``push_to_hub`` and ``setup_cluster``.
    """
    url = hub_url.rstrip("/") + CLUSTER_WEBHOOK_PATH
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"HUB_URL must use http:// or https:// scheme, got: {url}")
    body = json.dumps(payload, default=str).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": AGENT_USER_AGENT,
    }
    request = Request(url, data=body, headers=headers, method="POST")
    with safe_urlopen(request, allowed_hosts=settings.SSRF_ALLOWED_HOSTS, timeout=30) as response:
        return response.status, response.read().decode()


def _elapsed_ms(start: float) -> int:
    """Milliseconds elapsed since a ``time.perf_counter()`` start marker."""
    return int((time.perf_counter() - start) * 1000)


def summarize_push(
    *,
    hub_url: str,
    alerts: list[dict],
    http_status: int | None,
    duration_ms: int | None,
    ok: bool,
    error: str | None = None,
) -> str:
    """Build the concise push.log summary block. Pure — no I/O, no secrets.

    Never includes the payload/metrics or the API key; only the non-secret
    hub_url, counts, HTTP status, and the firing checker names.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dur = f" ({duration_ms}ms)" if duration_ms is not None else ""

    if not ok:
        if http_status is not None:
            detail = f"HTTP {http_status}"
        else:
            detail = f"unreachable: {error or 'unknown error'}"
        return f"{ts} push FAILED hub={hub_url} {detail}{dur}"

    firing = [a for a in alerts if a.get("status") == "firing"]
    n_ok = sum(1 for a in alerts if a.get("status") == "resolved")
    n_warn = sum(1 for a in firing if a.get("severity") == "warning")
    n_crit = sum(1 for a in firing if a.get("severity") == "critical")

    lines = [
        f"{ts} push OK hub={hub_url}",
        f"  ok={n_ok} warning={n_warn} critical={n_crit} -> {len(alerts)} alerts, "
        f"HTTP {http_status}{dur}",
    ]

    if firing:
        order: dict = {"critical": 0, "warning": 1}
        firing_sorted = sorted(firing, key=lambda a: order.get(a.get("severity"), 2))
        parts = [
            f"{a.get('labels', {}).get('checker', '?')}({a.get('severity', '?')})"
            for a in firing_sorted
        ]
        lines.append("  firing: " + ", ".join(parts))

    return "\n".join(lines)


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
        if not hub_url.rstrip("/").startswith(("https://", "http://")):
            raise CommandError(f"HUB_URL must use http:// or https:// scheme, got: {hub_url}")

        instance_id = getattr(settings, "INSTANCE_ID", "") or socket.gethostname()
        hostname = socket.gethostname()
        api_key = getattr(settings, "HUB_API_KEY", "")

        # Determine which checkers to run
        checker_names = None
        if options.get("checkers"):
            checker_names = [c.strip() for c in options["checkers"].split(",")]
            unknown = [n for n in checker_names if n not in CHECKER_REGISTRY]
            if unknown:
                raise CommandError(
                    f"Unknown checker(s): {', '.join(unknown)}. "
                    f"Available: {', '.join(sorted(CHECKER_REGISTRY))}"
                )

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
        payload = build_cluster_payload(instance_id, hostname, alerts)

        if options["dry_run"]:
            if options["json_output"]:
                self.stdout.write(json.dumps(payload, indent=2, default=str))
            else:
                self.stdout.write(self.style.NOTICE("Dry run — payload:"))
                self.stdout.write(json.dumps(payload, indent=2, default=str))
            return

        if not api_key:
            raise CommandError(
                "HUB_API_KEY is not configured. Set it in .env to enable agent mode "
                "(mint one on the hub with `manage.py create_api_key`)."
            )

        start = time.perf_counter()
        try:
            status, resp_body = send_to_hub(hub_url, api_key, payload)
        except HTTPError as e:
            # urllib raises HTTPError for non-2xx responses, so a real 4xx/5xx
            # lands here (not the else: branch below). Report it as an HTTP
            # failure, not "unreachable".
            self.stderr.write(
                summarize_push(
                    hub_url=hub_url,
                    alerts=alerts,
                    http_status=e.code,
                    duration_ms=_elapsed_ms(start),
                    ok=False,
                )
            )
            raise CommandError(f"Hub returned HTTP {e.code}")
        except URLNotAllowedError:
            self.stderr.write(
                summarize_push(
                    hub_url=hub_url,
                    alerts=alerts,
                    http_status=None,
                    duration_ms=_elapsed_ms(start),
                    ok=False,
                    error="URL not allowed by security policy",
                )
            )
            raise CommandError("HUB_URL not allowed by security policy")
        except Exception as e:
            self.stderr.write(
                summarize_push(
                    hub_url=hub_url,
                    alerts=alerts,
                    http_status=None,
                    duration_ms=_elapsed_ms(start),
                    ok=False,
                    error=str(e),
                )
            )
            raise CommandError(f"Failed to reach hub at {hub_url}: {e}")
        duration_ms = _elapsed_ms(start)

        if status in (200, 201, 202):
            if options["json_output"]:
                self.stdout.write(json.dumps(payload, indent=2, default=str))
            else:
                self.stdout.write(
                    summarize_push(
                        hub_url=hub_url,
                        alerts=alerts,
                        http_status=status,
                        duration_ms=duration_ms,
                        ok=True,
                    )
                )
        else:
            self.stderr.write(
                summarize_push(
                    hub_url=hub_url,
                    alerts=alerts,
                    http_status=status,
                    duration_ms=duration_ms,
                    ok=False,
                )
            )
            raise CommandError(f"Hub returned HTTP {status}: {resp_body}")

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
