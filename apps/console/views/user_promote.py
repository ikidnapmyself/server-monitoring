from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.console.queries.users import make_superuser
from apps.console.views.base import ConsoleView
from apps.console.views.user import user_not_found


class UserPromoteView(ConsoleView):
    """Make one active user a superuser, so they can sign in to the console. POST only."""

    http_method_names = ["post", "options"]

    def landing_path(self) -> str:
        return reverse("console:user", args=[self.kwargs["pk"]])

    def post(self, request, *args, **kwargs):
        outcome = make_superuser(kwargs["pk"])
        if outcome is None:
            return user_not_found(request)
        username, refusal = outcome
        if refusal:
            messages.warning(request, refusal)
        else:
            messages.success(request, f"{username} is now a superuser.")
        return HttpResponseRedirect(self.landing_path(), status=303)
