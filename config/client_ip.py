"""The client address behind the proxies listed in ``TRUSTED_PROXIES``."""

import ipaddress
from collections.abc import Collection, Sequence

from django.conf import settings

UNIX = "unix"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def invalid_entries(entries: Collection[str]) -> list[str]:
    return [e for e in entries if e != UNIX and _network(e) is None]


def client_ip(request) -> str:
    entries: Collection[str] = getattr(settings, "TRUSTED_PROXIES", ())
    networks = [net for e in entries if (net := _network(e)) is not None]
    peer = request.META.get("REMOTE_ADDR", "unknown")
    # gunicorn reports a unix-socket peer as an empty REMOTE_ADDR.
    peer_trusted = UNIX in entries if peer == "" else _trusted(_address(peer), networks)
    if not peer_trusted:
        return peer
    for hop in reversed(request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")):
        address = _address(hop.strip())
        if address is None:
            return peer
        if not _trusted(address, networks):
            return str(address)
    return peer


def _trusted(address: IPAddress | None, networks: Sequence[IPNetwork]) -> bool:
    return address is not None and any(address in net for net in networks)


def _address(value: str) -> IPAddress | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    # A zone id is free text, and it would end up in the rate limiter's cache key.
    if isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None:
        return None
    return address


def _network(entry: str) -> IPNetwork | None:
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None
