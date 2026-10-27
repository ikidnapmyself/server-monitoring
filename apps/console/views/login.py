from django.contrib.auth.views import LoginView
from django.shortcuts import render

from apps.console.forms import ConsoleLoginForm


class ConsoleLoginView(LoginView):
    """Django's own login view, so it isn't a ``ConsoleView``: anyone may open it."""

    template_name = "console/login.html"
    authentication_form = ConsoleLoginForm
    redirect_authenticated_user = True

    @classmethod
    def rate_limited(cls, request, retry_after: int):
        """The page ``RateLimitMiddleware`` shows once sign-in attempts run out."""
        context = {
            "form": cls.authentication_form(request),
            "next": request.POST.get("next", ""),
            "refusal": f"Too many sign-in attempts. Try again in {retry_after} seconds.",
        }
        return render(request, cls.template_name, context, status=429)
