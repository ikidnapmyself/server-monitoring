"""Safe HTTP client — drop-in replacement for urllib.request.urlopen with SSRF protection."""

import urllib.request  # noqa: TID251

from config.security.url_validation import validate_safe_url


class _SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):  # noqa: TID251
    """HTTP redirect handler that validates each redirect target for SSRF safety.

    Prevents redirect-based SSRF attacks where a public URL redirects to a
    private/internal address (e.g., 169.254.169.254 cloud metadata endpoints).
    """

    def __init__(self, allowed_hosts=()):
        super().__init__()
        self._allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_safe_url(newurl, allowed_hosts=self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(request, *, allowed_hosts=(), timeout=30):
    """SSRF-safe replacement for urllib.request.urlopen.

    Validates the request URL against private/reserved IP ranges before
    making the HTTP request, and validates each redirect target to prevent
    redirect-based SSRF bypasses. Use this instead of urllib.request.urlopen
    in all application code.

    Args:
        request: A urllib.request.Request object or URL string.
        allowed_hosts: Tuple of hostnames/IPs that bypass the private-IP check.
        timeout: Request timeout in seconds.

    Returns:
        The response from the opener.

    Raises:
        URLNotAllowedError: If the URL (or any redirect target) resolves to a
            private/reserved IP.
    """
    url = request.full_url if hasattr(request, "full_url") else str(request)
    validate_safe_url(url, allowed_hosts=allowed_hosts)
    opener = urllib.request.build_opener(  # noqa: TID251
        _SSRFRedirectHandler(allowed_hosts=allowed_hosts)
    )
    return opener.open(request, timeout=timeout)
