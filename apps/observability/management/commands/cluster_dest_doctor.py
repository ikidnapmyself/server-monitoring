"""manage.py cluster_dest_doctor <name> — diagnose one cluster destination.

Runs a short ladder of probes against the destination's hub URL:

    1. DNS resolves for the hub hostname.
    2. TCP connect succeeds on the URL's port.
    3. TLS handshake completes (only if the scheme is ``https``).
    4. ``HEAD {hub_url}/cluster/logs/health/`` is reachable.

Each probe prints ``[✓]`` / ``[✗]`` followed by a short detail. The command
exits 0 if every probe that ran passed and 1 otherwise. Probes are run in
order and the ladder stops at the first failure — this keeps the output
focused on the earliest reason a destination is unreachable.

Auth on the HTTP probe is opt-in. The stored ``APIKey`` only keeps a hash of
the credential, so the raw key cannot be recovered from the destination row;
the operator supplies it via ``--secret`` or the ``CLUSTER_HUB_SECRET`` env
var (preferred — it keeps the secret out of shell history and ``ps``). When a
secret is given it is sent as ``Authorization: Bearer <secret>`` (RFC 6750).
Without one, the probe only checks reachability and reports a ``401``/``403``
as "auth required" rather than a credential failure.

``--json`` produces a structured payload of the same checks for scripts.
"""

from __future__ import annotations

import json
import os
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
        parser.add_argument(
            "--secret",
            default=None,
            help=(
                "Raw API key for the HTTP probe, sent as 'Authorization: Bearer "
                "<secret>'. Falls back to the CLUSTER_HUB_SECRET env var (preferred, "
                "to keep the secret out of shell history). Omit to probe reachability "
                "only — the stored APIKey holds only a hash, so the raw key cannot be "
                "recovered from the destination."
            ),
        )

    def handle(self, *args, **options):
        dest = get_destination_or_raise(options["name"])
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
            ok, detail = _tls_probe(host, port, timeout)
            checks.append(("tls", ok, detail))
            if not ok:
                return self._finalize(dest.name, checks, json_mode)

        # 4. HTTP
        secret = options["secret"] or os.environ.get("CLUSTER_HUB_SECRET")
        url = dest.hub_url.rstrip("/") + HEALTH_PATH
        req = Request(url, method="HEAD")
        if secret:
            req.add_header("Authorization", f"Bearer {secret}")
        try:
            with safe_urlopen(
                req,
                allowed_hosts=getattr(settings, "SSRF_ALLOWED_HOSTS", ()),
                timeout=timeout,
            ) as resp:
                status = resp.status
            checks.append(("http", True, f"HTTP {status}"))
        except HTTPError as exc:
            checks.append(("http", False, _http_error_detail(exc.code, url, bool(secret))))
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


def _tls_probe(host: str, port: int, timeout: int) -> tuple[bool, str]:
    """Probe a TLS handshake to ``host:port``, returning ``(ok, detail)``.

    Closes its socket on every path so the probe never leaks an FD: if
    ``wrap_socket`` fails it closes the raw socket directly; on success the
    ``finally`` closes the SSLSocket (which closes the underlying socket too).
    """
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return False, str(exc)
    try:
        ctx = ssl.create_default_context()
        wrapped = ctx.wrap_socket(sock, server_hostname=host)
    except (ssl.SSLError, OSError) as exc:
        sock.close()
        return False, str(exc)
    try:
        return True, _tls_detail(wrapped.getpeercert())
    finally:
        wrapped.close()


def _tls_detail(cert) -> str:
    if cert is None:
        return "(no cert)"
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return value
    return "(no CN)"


def _http_error_detail(code: int, url: str, authenticated: bool) -> str:
    if code in (401, 403):
        if authenticated:
            return f"auth rejected (HTTP {code}) — check the API key"
        return f"auth required (HTTP {code}) — pass --secret/CLUSTER_HUB_SECRET to authenticate"
    if code == 404:
        return f"endpoint not found (HTTP 404) at {url}"
    if code >= 500:
        return f"hub error: {code}"
    return f"HTTP {code}"
