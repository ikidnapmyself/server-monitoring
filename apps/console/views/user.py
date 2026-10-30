from django.contrib import messages
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.queries.users import UserEditor, build_user_editor, update_user
from apps.console.views.base import ConsoleView, Notice

NOT_ADDED = "Couldn't add the user just now. Nothing was saved, so try again."
BUSY = "Couldn't save just now. Nothing was saved, so try again."


def user_not_found(request):
    context = {"nav": "system", "section": "users"}
    return render(request, "console/not_found.html", context, status=404)


class UserView(ConsoleView):
    """Edit one user, or add one. A new user is always an active superuser."""

    template_name = "console/user.html"
    partial_template_name = "console/partials/_user.html"

    @property
    def pk(self) -> int | None:
        return self.kwargs.get("pk")

    def landing_path(self) -> str:
        if self.pk is None:
            return reverse("console:user-new")
        return reverse("console:user", args=[self.pk])

    def render_editor(
        self, editor: UserEditor | None, notice: Notice | None = None, status: int = 200
    ):
        if editor is None:
            return user_not_found(self.request)
        context = self.get_context_data(editor=editor, nav="system", section="users", notice=notice)
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        return self.render_editor(build_user_editor(self.pk, actor=request.user))

    def post(self, request, *args, **kwargs):
        editor = build_user_editor(self.pk, request.POST, actor=request.user)
        if editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        if self.pk is None:
            return self.create(editor)
        return self.update(self.pk, editor)

    def create(self, editor: UserEditor):
        try:
            with transaction.atomic():
                user = editor.form.save()
        except (IntegrityError, OperationalError):
            return self.render_editor(editor, Notice(NOT_ADDED, "warning"), 409)
        messages.success(self.request, f"User {user.username} added.")
        return HttpResponseRedirect(reverse("console:user", args=[user.pk]), status=303)

    def update(self, pk: int, editor: UserEditor):
        try:
            refusal = update_user(self.request.user, editor.form)
        except (IntegrityError, OperationalError):
            refusal = BUSY
        if refusal is None:
            return user_not_found(self.request)
        if refusal:
            # The bound form keeps what the operator typed. None of it was saved.
            return self.render_editor(editor, Notice(refusal, "warning"), 409)
        messages.success(self.request, f"User {editor.form.cleaned_data['username']} saved.")
        return HttpResponseRedirect(reverse("console:user", args=[pk]), status=303)
