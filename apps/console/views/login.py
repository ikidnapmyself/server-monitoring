from urllib.parse import urlsplit

from django.contrib.auth.views import LoginView
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.console.forms import ConsoleLoginForm


class ConsoleLoginView(LoginView):
    """Django's own login view, so it isn't a ``ConsoleView``: anyone may open it."""

    template_name = "console/login.html"
    authentication_form = ConsoleLoginForm
    redirect_authenticated_user = True

    def get_redirect_url(self):
        # A next back to this page would make Django raise on a redirect loop.
        url = super().get_redirect_url()
        return "" if urlsplit(url).path == self.request.path else url

    @classmethod
    def rate_limited(cls, request, retry_after: int):
        """The page ``RateLimitMiddleware`` shows once sign-in attempts run out."""
        wait = f"{retry_after} second{'' if retry_after == 1 else 's'}"
        next_url = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            next_url = ""
        context = {
            "form": cls.authentication_form(
                request, initial={"username": request.POST.get("username", "")}
            ),
            "next": next_url,
            "refusal": f"Too many sign-in attempts. Try again in {wait}.",
        }
        return render(request, cls.template_name, context, status=429)
