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

Each socket is enriched with its owning process ``name`` and ``username`` (best
effort via ``psutil.Process``) so an operator can tell what is bound to a port.
Command line is intentionally not captured (it can carry secrets).
"""

import sys
from dataclasses import dataclass
from typing import Any

import psutil
from django.conf import settings

from apps.checkers.checkers.base import BaseChecker, CheckResult, CheckStatus
from config.describing import Action, Description, format_list


@dataclass
class ListeningPort:
    """One LISTENing socket."""

    ip: str
    port: int
    family: str
    pid: int | None
    exposed: bool
    process: str | None = None
    username: str | None = None


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


def _process_info(pid: int) -> dict:
    """Resolve a PID to {name, username}; empty dict if it can't be read.

    ``as_dict(ad_value=None)`` yields None for individual inaccessible fields; a
    vanished/denied process is swallowed so resolution never raises.
    """
    try:
        return psutil.Process(pid).as_dict(attrs=["name", "username"], ad_value=None)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return {}


def resolve_processes(ports: list[ListeningPort]) -> None:
    """Stamp process name + owning username onto each port, in place.

    Resolves each unique PID once — sockets share PIDs (IPv4+IPv6, pre-fork
    workers). A None PID or an unresolvable process leaves the fields as None.
    """
    cache: dict[int, dict] = {}
    for port in ports:
        if port.pid is None:
            continue
        info = cache.get(port.pid)
        if info is None:
            info = cache[port.pid] = _process_info(port.pid)
        port.process = info.get("name")
        port.username = info.get("username")


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
    label = "Listening ports"
    problem = "Unexpected open ports"

    def check(self) -> CheckResult:
        if sys.platform != "linux":
            return self._skip("not Linux")

        try:
            ports = collect_listening()
        except (psutil.AccessDenied, PermissionError):
            return self._skip("cannot read listening ports")

        resolve_processes(ports)
        allowlist = {int(p) for p in getattr(settings, "LISTENING_PORTS_ALLOWLIST", []) or []}
        flagged = flagged_ports(ports, allowlist)

        if flagged:
            status = CheckStatus.WARNING
            shown = ", ".join(f"{p.port}({p.ip}) [{p.process or '?'}]" for p in flagged)
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
            status=CheckStatus.SKIPPED,
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
                    "process": p.process,
                    "username": p.username,
                }
                for p in ports
            ],
        }

    def describe_result(self, status: CheckStatus, metrics: dict[str, Any]) -> Description:
        if status is CheckStatus.OK:
            count = metrics["listening_count"]
            ports = "port" if count == 1 else "ports"
            return self._describe(status, f"{count} {ports} listening, all expected", metrics)
        # Hub-side re-evaluation rescores status against the node's allowlist without
        # touching metrics, so a firing result can carry no node-flagged ports.
        unexpected = sorted(set(metrics["unexpected_ports"]))
        if not unexpected:
            return self._describe(
                status,
                "Ports open outside this node's allowlist",
                metrics,
                action=Action(
                    "Close the ports this node shouldn't expose, or add them to its port allowlist."
                ),
            )
        ports = format_list((str(port) for port in unexpected), limit=len(unexpected))
        them = "them" if len(unexpected) > 1 else "it"
        return self._describe(
            status,
            f"Unexpected ports open: {ports}",
            metrics,
            action=Action(f"Close {ports}, or add {them} to this node's port allowlist."),
        )
