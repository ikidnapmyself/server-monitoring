"""manage.py cluster_dest_doctor <name> — diagnose one cluster destination.

Runs a short ladder of probes against the destination's hub URL:

    1. DNS resolves for the hub hostname.
    2. TCP connect succeeds on the URL's port.
    3. TLS handshake completes (only if the scheme is ``https``).
    4. ``HEAD {hub_url}/cluster/logs/health/`` returns a 2xx with auth.

Each probe prints ``[✓]`` / ``[✗]`` followed by a short detail. The command
exits 0 if every probe that ran passed and 1 otherwise. Probes are run in
order and the ladder stops at the first failure — this keeps the output
focused on the earliest reason a destination is unreachable.

``--json`` produces a structured payload of the same checks for scripts.
"""

from __future__ import annotations

import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request  # noqa: TID251 — Request is a data object, not urlopen

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.observability.management.commands._cluster_dest_common import (
    get_destination_or_raise,
)
from config.security.http import safe_urlopen

HEALTH_PATH = "/cluster/logs/health/"


class Command(BaseCommand):
    help = "Diagnose connectivity and auth against one cluster log-push destination."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Destination name.")
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="Per-probe timeout in seconds (default: 30).",
        )

    def handle(self, *args, **options):
        dest = get_destination_or_raise(options["name"], select_api_key=True)
        timeout = options["timeout"]
        json_mode = options["json"]

        parsed = urlparse(dest.hub_url)
        host = parsed.hostname or ""
        scheme = (parsed.scheme or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80)

        checks: list[tuple[str, bool, str]] = []

        # 1. DNS
        try:
            ip = socket.gethostbyname(host)
        except socket.gaierror as exc:
            checks.append(("dns", False, str(exc)))
            return self._finalize(dest.name, checks, json_mode)
        checks.append(("dns", True, ip))

        # 2. TCP
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
        except OSError as exc:
            checks.append(("tcp", False, str(exc)))
            return self._finalize(dest.name, checks, json_mode)
        checks.append(("tcp", True, f"{host}:{port}"))

        # 3. TLS (https only)
        if scheme == "https":
            try:
                tls_sock = socket.create_connection((host, port), timeout=timeout)
                ctx = ssl.create_default_context()
                wrapped = ctx.wrap_socket(tls_sock, server_hostname=host)
                cert = wrapped.getpeercert()
            except (ssl.SSLError, OSError) as exc:
                checks.append(("tls", False, str(exc)))
                return self._finalize(dest.name, checks, json_mode)
            checks.append(("tls", True, _tls_detail(cert)))

        # 4. HTTP
        url = dest.hub_url.rstrip("/") + HEALTH_PATH
        req = Request(url, method="HEAD")
        req.add_header("Authorization", f"ApiKey {dest.api_key.name}")
        try:
            with safe_urlopen(
                req,
                allowed_hosts=getattr(settings, "SSRF_ALLOWED_HOSTS", ()),
                timeout=timeout,
            ) as resp:
                status = resp.status
            checks.append(("http", True, f"HTTP {status}"))
        except HTTPError as exc:
            checks.append(("http", False, _http_error_detail(exc.code)))
        except URLError as exc:
            checks.append(("http", False, str(exc.reason)))

        return self._finalize(dest.name, checks, json_mode)

    def _finalize(
        self,
        dest_name: str,
        checks: list[tuple[str, bool, str]],
        json_mode: bool,
    ) -> None:
        ok = sum(1 for _, passed, _ in checks if passed)
        total = len(checks)
        all_passed = ok == total

        if json_mode:
            payload = {
                "destination": dest_name,
                "checks": [
                    {"name": name, "ok": passed, "detail": detail}
                    for name, passed, detail in checks
                ],
                "summary": {"ok": ok, "fail": total - ok, "total": total},
            }
            self.stdout.write(json.dumps(payload))
        else:
            for name, passed, detail in checks:
                marker = "[✓]" if passed else "[✗]"
                self.stdout.write(f"{marker} {name}: {detail}")
            self.stdout.write(f"Summary: {ok}/{total} checks passed")

        if not all_passed:
            raise SystemExit(1)


def _tls_detail(cert) -> str:
    if cert is None:
        return "(no cert)"
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return value
    return "(no CN)"


def _http_error_detail(code: int) -> str:
    if code in (401, 403):
        return f"auth rejected (HTTP {code})"
    if code == 404:
        return "endpoint not found (will exist in PR 2)"
    if code >= 500:
        return f"hub error: {code}"
    return f"HTTP {code}"
