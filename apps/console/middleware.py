"""Response headers for everything served under the console prefix."""

from django.utils.cache import add_never_cache_headers, patch_vary_headers

CONSOLE_PATH_PREFIX = "/console/"

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class ConsoleHeadersMiddleware:
    """Strict CSP, ``Vary: HX-Request`` and never-cache on every console response.

    Lives in middleware, not the view, so a 404, a 500 or a CSRF refusal under the
    prefix carries the same headers as a page a console view returned.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith(CONSOLE_PATH_PREFIX):
            return response

        response["Content-Security-Policy"] = CSP
        patch_vary_headers(response, ["HX-Request"])
        add_never_cache_headers(response)
        return response
