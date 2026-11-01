"""The client address behind the proxies listed in ``TRUSTED_PROXIES``."""

import ipaddress
from collections.abc import Iterable

from django.conf import settings

UNIX = "unix"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def invalid_entries(entries: Iterable[str]) -> list[str]:
    return [e for e in entries if e != UNIX and _network(e) is None]


def client_ip(request) -> str:
    entries = settings.TRUSTED_PROXIES
    peer = request.META.get("REMOTE_ADDR", "unknown")
    if not _trusted(peer, entries):
        return peer
    for hop in reversed(request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")):
        address = _address(hop.strip())
        if address is None:
            return peer
        if not _trusted(str(address), entries):
            return str(address)
    return peer


def _trusted(addr: str, entries: Iterable[str]) -> bool:
    # gunicorn reports a unix-socket peer as an empty REMOTE_ADDR.
    if addr == "":
        return UNIX in entries
    address = _address(addr)
    if address is None:
        return False
    return any(address in net for e in entries if (net := _network(e)) is not None)


def _address(value: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _network(entry: str) -> IPNetwork | None:
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None
