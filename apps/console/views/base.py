from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import reverse
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.views.generic import TemplateView

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class ConsoleView(TemplateView):
    """Base for every console page. Staff only, CSP on every response.

    One URL serves two sizes: the full page, and only ``partial_template_name``
    when htmx asks. Subclasses must set both template names.
    """

    template_name: str
    partial_template_name: str

    def dispatch(self, request, *args, **kwargs):
        response = self._refusal(request) or super().dispatch(request, *args, **kwargs)
        response["Content-Security-Policy"] = CSP
        patch_vary_headers(response, ["HX-Request"])
        add_never_cache_headers(response)
        return response

    @property
    def is_htmx(self) -> bool:
        return self.request.headers.get("HX-Request") == "true"

    def get_template_names(self) -> list[str]:
        if self.is_htmx:
            return [self.partial_template_name]
        return [self.template_name]

    def _refusal(self, request):
        if not request.user.is_authenticated:
            redirect = redirect_to_login(request.get_full_path(), reverse("admin:login"))
            if not self.is_htmx:
                return redirect
            # htmx would swap a followed redirect's login form into the page.
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect.url
            return response
        if not (request.user.is_active and request.user.is_staff):
            return HttpResponseForbidden("Staff access required.")
        return None
