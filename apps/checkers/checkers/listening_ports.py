"""Listening-port audit checker for Linux (``psutil.net_connections``).

Enumerates the host's LISTENing TCP/UDP sockets and flags ports that are not in
an operator-defined allowlist. Linux-gated: reads ``/proc/net`` without root on
Linux; skips as OK on non-Linux (where ``net_connections`` needs root).

Policy (see docs/plans design discussion 2026-08-07):
- Allowlist comes from ``settings.LISTENING_PORTS_ALLOWLIST`` (a settings
  constant, never a caller-supplied kwarg — target lists must not flow from
  untrusted provider_config).
- With an allowlist set: any listening port not in it is WARNING, regardless of
  bind address (loopback included).
- With no allowlist: only externally-exposed ports (non-loopback bind) are
  flagged WARNING; loopback-only ports are inventoried but not alarmed.
"""

import sys
from dataclasses import dataclass

import psutil
from django.conf import settings

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus


@dataclass
class ListeningPort:
    """One LISTENing socket."""

    ip: str
    port: int
    family: str
    pid: int | None
    exposed: bool


def _is_exposed(ip: str) -> bool:
    """True if the bind address is externally reachable (not loopback)."""
    return not (ip.startswith("127.") or ip == "::1")


def collect_listening() -> list[ListeningPort]:
    """Return the host's LISTENing sockets (performs the psutil read)."""
    ports: list[ListeningPort] = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
            continue
        ip = conn.laddr.ip
        ports.append(
            ListeningPort(
                ip=ip,
                port=conn.laddr.port,
                family="ipv6" if ":" in ip else "ipv4",
                pid=conn.pid,
                exposed=_is_exposed(ip),
            )
        )
    return ports


def flagged_ports(ports: list[ListeningPort], allowlist: set[int]) -> list[ListeningPort]:
    """Return the ports that violate policy.

    With an allowlist: every port not in it. Without an allowlist: only exposed
    (non-loopback) ports.
    """
    flagged: list[ListeningPort] = []
    for p in ports:
        if p.port in allowlist:
            continue
        if allowlist or p.exposed:
            flagged.append(p)
    return flagged


class ListeningPortsChecker(BaseChecker):
    """Audit the host's listening ports against an allowlist."""

    name = "listening_ports"

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        try:
            ports = collect_listening()
        except (psutil.AccessDenied, PermissionError):
            return self._skip("cannot read listening ports")

        allowlist = {int(p) for p in getattr(settings, "LISTENING_PORTS_ALLOWLIST", []) or []}
        flagged = flagged_ports(ports, allowlist)

        if flagged:
            status = CheckStatus.WARNING
            shown = ", ".join(f"{p.port}({p.ip})" for p in flagged)
            message = f"{len(flagged)} unexpected listening port(s): {shown}"
        else:
            status = CheckStatus.OK
            message = f"{len(ports)} listening port(s), none unexpected"

        return self._make_result(
            status=status,
            message=message,
            metrics=self._metrics(ports, flagged, allowlist),
        )

    def _skip(self, reason: str) -> CheckResult:
        return self._make_result(
            status=CheckStatus.OK,
            message=f"Skipped: {reason}",
            metrics=self._metrics([], [], set()),
        )

    def _metrics(
        self,
        ports: list[ListeningPort],
        flagged: list[ListeningPort],
        allowlist: set[int],
    ) -> dict:
        return {
            "platform": sys.platform,
            "listening_count": len(ports),
            "allowlist": sorted(allowlist),
            "unexpected_ports": [p.port for p in flagged],
            "listening": [
                {
                    "port": p.port,
                    "address": p.ip,
                    "family": p.family,
                    "pid": p.pid,
                    "exposed": p.exposed,
                }
                for p in ports
            ],
        }
