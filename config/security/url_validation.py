"""SSRF prevention — validate outbound URLs against private/reserved IP ranges."""

import ipaddress
import socket
import urllib.parse


class URLNotAllowedError(ValueError):
    """Raised when a URL targets a private/reserved network address."""


def _redact_url(url: str) -> str:
    """Return a redacted URL string containing only scheme, hostname, and port.

    Path, query, and fragment are omitted to avoid leaking tokens or secrets
    that may appear in webhook URLs or signed endpoints. Returns "<invalid URL>"
    if the URL is empty or cannot be parsed.
    """
    if not url:
        return "<invalid URL>"
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return f"{parsed.scheme}://{netloc}"
    except Exception:
        return "<invalid URL>"


def validate_safe_url(
    url: str,
    allowed_hosts: tuple[str, ...] = (),
) -> str:
    """Validate that a URL does not resolve to a private/reserved IP.

    Returns the original URL if safe. Raises URLNotAllowedError if the URL
    targets a blocked address or cannot be resolved.
    """
    if not url:
        raise URLNotAllowedError("URL not allowed: empty URL")

    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise URLNotAllowedError(
            f"URL not allowed: scheme must be http or https, got {parsed.scheme!r}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise URLNotAllowedError("URL not allowed: hostname is missing")

    # Allowlist bypass — trusted internal hosts
    if hostname in allowed_hosts:
        return url

    # Resolve DNS and check all resulting IPs
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443)
    except OSError as exc:
        raise URLNotAllowedError(
            f"URL not allowed: could not resolve hostname {hostname!r}: {exc}"
        ) from exc

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)

        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise URLNotAllowedError(
                f"URL not allowed: hostname {hostname!r} resolves to "
                f"private/reserved address {ip_str}"
            )

    return url
