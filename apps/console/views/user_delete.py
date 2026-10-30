from django.contrib import messages
from django.db import IntegrityError, OperationalError
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.console.queries.users import build_user_deletion, delete_user, user_name
from apps.console.views.base import ConsoleView
from apps.console.views.user import user_not_found


class UserDeleteView(ConsoleView):
    """Confirm deleting one user, or say why they can't be, then delete them."""

    template_name = "console/user_delete.html"
    partial_template_name = "console/partials/_user_delete.html"

    def landing_path(self) -> str:
        return reverse("console:user", args=[self.kwargs["pk"]])

    def get(self, request, *args, **kwargs):
        deletion = build_user_deletion(request.user, kwargs["pk"])
        if deletion is None:
            return user_not_found(request)
        context = self.get_context_data(deletion=deletion, nav="system", section="users")
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        username = user_name(kwargs["pk"])
        if username is None:
            return user_not_found(request)
        back = HttpResponseRedirect(self.landing_path(), status=303)
        try:
            refusal = delete_user(request.user, kwargs["pk"])
        except (IntegrityError, OperationalError):
            messages.warning(
                request, f"Couldn't delete {username} just now. Nothing was deleted, so try again."
            )
            return back
        if refusal is None:
            return user_not_found(request)
        if refusal:
            messages.warning(request, refusal)
            return back
        messages.success(request, f"User {username} deleted.")
        return HttpResponseRedirect(reverse("console:users"), status=303)
