"""Safe HTTP client — drop-in replacement for urllib.request.urlopen with SSRF protection."""

import urllib.request

from config.security.url_validation import validate_safe_url


def safe_urlopen(request, *, allowed_hosts=(), timeout=30):
    """SSRF-safe replacement for urllib.request.urlopen.

    Validates the request URL against private/reserved IP ranges before
    making the HTTP request. Use this instead of urllib.request.urlopen
    in all application code.

    Args:
        request: A urllib.request.Request object or URL string.
        allowed_hosts: Tuple of hostnames/IPs that bypass the private-IP check.
        timeout: Request timeout in seconds.

    Returns:
        The response from urllib.request.urlopen.

    Raises:
        URLNotAllowedError: If the URL resolves to a private/reserved IP.
    """
    url = request.full_url if hasattr(request, "full_url") else str(request)
    validate_safe_url(url, allowed_hosts=allowed_hosts)
    return urllib.request.urlopen(request, timeout=timeout)
