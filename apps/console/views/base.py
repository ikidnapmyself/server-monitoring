from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView

from apps.console.intents import admin_url
from apps.console.middleware import CONSOLE_PATH_PREFIX

NOTHING_TO_APPLY = "Nothing here needs re-evaluating, so nothing was applied."


@dataclass(frozen=True)
class Notice:
    """The outcome of an action, shown once in the response that carried it out."""

    text: str
    tone: str


class ConsoleView(TemplateView):
    """Base for every console page. Active superusers only.

    One URL serves two sizes: the full page, and only ``partial_template_name``
    when htmx asks for a fragment. Subclasses must set both template names.
    Response headers belong to ``ConsoleHeadersMiddleware``, not to this view.
    """

    template_name: str
    partial_template_name: str
    read_permissions: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        return self._refusal(request) or super().dispatch(request, *args, **kwargs)

    @property
    def is_htmx(self) -> bool:
        return self.request.headers.get("HX-Request") == "true"

    @property
    def wants_partial(self) -> bool:
        # History restores and boosted navigation swap the response into <body>,
        # so a fragment there would replace the whole layout.
        headers = self.request.headers
        return (
            self.is_htmx
            and headers.get("HX-History-Restore-Request") != "true"
            and headers.get("HX-Boosted") != "true"
        )

    def get_template_names(self) -> list[str]:
        if self.wants_partial:
            return [self.partial_template_name]
        return [self.template_name]

    def _refusal(self, request):
        if not request.user.is_authenticated:
            login_url = admin_url("login")
            if not self.is_htmx:
                return redirect_to_login(self._return_path(request), login_url)
            # htmx would swap a followed redirect's login form into the page.
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_to_login(self._page_path(request), login_url).url
            return response
        if not (request.user.is_active and request.user.is_superuser):
            return render(request, "console/not_superuser.html", status=403)
        if self.read_permissions and not any(map(request.user.has_perm, self.read_permissions)):
            return HttpResponseForbidden("Permission to view this page required.")
        return None

    def landing_path(self) -> str:
        """Where a login lands after a request that can't be replayed as a GET."""
        return reverse("console:overview")

    def _return_path(self, request) -> str:
        if request.method in ("GET", "HEAD"):
            return request.get_full_path()
        return self.landing_path()

    def _page_path(self, request) -> str:
        """Local path of the page an htmx request came from, for the login ``next``.

        The request URL itself may be a fragment or a POST-only action.
        """

        def is_local(url: str) -> bool:
            return url_has_allowed_host_and_scheme(
                url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            )

        current = request.headers.get("HX-Current-URL", "")
        if is_local(current):
            parts = urlsplit(current)
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            # A browser collapses dot segments, also encoded or behind a backslash.
            segments = unquote(parts.path).replace("\\", "/").split("/")
            # A same-host URL can still carry a path like //evil.example/x.
            if (
                is_local(path)
                and parts.path.startswith(CONSOLE_PATH_PREFIX)
                and ".." not in segments
            ):
                return path
        return self._return_path(request)
