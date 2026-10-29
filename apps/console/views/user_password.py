from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.console.queries.users import PasswordEditor, build_password_editor
from apps.console.views.base import ConsoleView
from apps.console.views.user import user_not_found


class UserPasswordView(ConsoleView):
    """Set a user's password. Setting your own keeps you signed in."""

    template_name = "console/user_password.html"
    partial_template_name = "console/partials/_user_password.html"

    def landing_path(self) -> str:
        return reverse("console:user-password", args=[self.kwargs["pk"]])

    def render_editor(self, editor: PasswordEditor | None):
        if editor is None:
            return user_not_found(self.request)
        context = self.get_context_data(editor=editor, nav="system", section="users")
        return self.render_to_response(context)

    def get(self, request, *args, **kwargs):
        return self.render_editor(build_password_editor(kwargs["pk"]))

    def post(self, request, *args, **kwargs):
        editor = build_password_editor(kwargs["pk"], request.POST)
        if editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        user = editor.form.save()
        if user.pk == request.user.pk:
            update_session_auth_hash(request, user)
        messages.success(request, f"Password set for {user.username}.")
        return HttpResponseRedirect(reverse("console:user", args=[user.pk]), status=303)
