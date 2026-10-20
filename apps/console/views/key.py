from django.contrib import messages
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.intents import KEY_ADD, KEY_CHANGE, KEY_READ
from apps.console.queries.keys import KeyEditor, build_key_editor
from apps.console.views.base import ConsoleView, Notice
from config.api_keys import KEY_FIELDS, StaleKey, save_key
from config.models import APIKey

STALE = "This key changed since you opened it. Here it is now."
BUSY = "Couldn't save just now. Here's the key as it is."
NOT_ADDED = "Couldn't add the key just now. Nothing was saved, so try again."
SAVED = "Key saved."


class KeyView(ConsoleView):
    """Edit one API key, or add one and show its token once."""

    template_name = "console/key.html"
    partial_template_name = "console/partials/_key.html"
    read_permissions = KEY_READ

    @property
    def pk(self) -> int | None:
        return self.kwargs.get("pk")

    @property
    def can_change(self) -> bool:
        return self.request.user.has_perm(KEY_ADD if self.pk is None else KEY_CHANGE)

    def landing_path(self) -> str:
        if self.pk is None:
            return reverse("console:key-new")
        return reverse("console:key", args=[self.pk])

    def forbidden(self):
        verb = "add" if self.pk is None else "change"
        return HttpResponseForbidden(f"Permission to {verb} API keys required.")

    def not_found(self):
        context = {"nav": "system", "section": "keys"}
        return render(self.request, "console/not_found.html", context, status=404)

    def render_editor(
        self, editor: KeyEditor | None, notice: Notice | None = None, status: int = 200
    ):
        if editor is None:
            return self.not_found()
        context = self.get_context_data(editor=editor, nav="system", section="keys", notice=notice)
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        if self.pk is None and not self.can_change:
            return self.forbidden()
        return self.render_editor(build_key_editor(self.pk, can_change=self.can_change))

    def post(self, request, *args, **kwargs):
        if not self.can_change:
            return self.forbidden()
        digest = request.POST.get("digest", "")
        editor = build_key_editor(self.pk, request.POST, can_change=True, digest=digest)
        if editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        values = {field: editor.form.cleaned_data[field] for field in KEY_FIELDS}
        if self.pk is None:
            return self.create(editor, values)
        return self.update(self.pk, values, digest)

    def create(self, editor: KeyEditor, values: dict):
        try:
            with transaction.atomic():
                key = APIKey.objects.create(**values)
        except (IntegrityError, OperationalError):
            return self.render_editor(editor, Notice(NOT_ADDED, "warning"), 409)
        context = self.get_context_data(
            key=key,
            token=key._raw_key,
            key_url=reverse("console:key", args=[key.pk]),
            nav="system",
            section="keys",
        )
        response = render(self.request, "console/key_created.html", context)
        response["Cache-Control"] = "no-store"
        return response

    def update(self, pk: int, values: dict, digest: str):
        try:
            with transaction.atomic():
                save_key(pk, values, expected=digest)
        except StaleKey:
            return self.refresh(pk, STALE)
        except (IntegrityError, OperationalError):
            return self.refresh(pk, BUSY)
        messages.success(self.request, SAVED)
        return HttpResponseRedirect(reverse("console:key", args=[pk]), status=303)

    def refresh(self, pk: int, notice: str):
        fresh = build_key_editor(pk, can_change=True)
        return self.render_editor(fresh, Notice(notice, "warning"), 409)
