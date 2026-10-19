from django.contrib import messages
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.intents import PROVIDER_ADD, PROVIDER_CHANGE, PROVIDER_READ
from apps.console.queries.providers import (
    ProviderEditor,
    UnknownProvider,
    build_provider_editor,
    provider_choices,
)
from apps.console.views.base import ConsoleView, Notice
from apps.intelligence.forms import NAME_TAKEN
from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.saving import StaleProvider, save_provider

STALE = "This provider changed since you opened it. Here it is now."
BUSY = "Couldn't save just now. Here's the provider as it is."
NOT_ADDED = "Couldn't add the provider just now. Nothing was saved, so try again."
NO_SUCH_PROVIDER = "This hub has no provider by that name. Pick one it has."
SAVED = "Provider saved."
ADDED = "Provider added."


class ProviderView(ConsoleView):
    """Edit one provider, or add one. The rules live in ``ProviderForm`` and ``save_provider``."""

    template_name = "console/provider.html"
    partial_template_name = "console/partials/_provider.html"
    read_permissions = PROVIDER_READ

    @property
    def pk(self) -> int | None:
        return self.kwargs.get("pk")

    @property
    def can_change(self) -> bool:
        return self.request.user.has_perm(PROVIDER_ADD if self.pk is None else PROVIDER_CHANGE)

    def landing_path(self) -> str:
        if self.pk is None:
            return reverse("console:provider-new")
        return reverse("console:provider", args=[self.pk])

    def forbidden(self):
        return HttpResponseForbidden("Permission to change providers required.")

    def not_found(self):
        context = {"nav": "system", "section": "providers"}
        return render(self.request, "console/not_found.html", context, status=404)

    def picker(self, notice: Notice | None = None, status: int = 200):
        context = self.get_context_data(
            kinds=provider_choices(), nav="system", section="providers", notice=notice
        )
        return render(self.request, "console/provider_new.html", context, status=status)

    def render_editor(
        self, editor: ProviderEditor | None, notice: Notice | None = None, status: int = 200
    ):
        if editor is None:
            return self.not_found()
        context = self.get_context_data(
            editor=editor, nav="system", section="providers", notice=notice
        )
        return self.render_to_response(context, status=status)

    def unknown_provider(self):
        notice = Notice(NO_SUCH_PROVIDER, "warning")
        if self.pk is None:
            return self.picker(notice, 400)
        editor = build_provider_editor(self.pk, can_change=self.can_change)
        return self.render_editor(editor, notice, 400)

    def landed(self, message: str, pk: int):
        messages.success(self.request, message)
        return HttpResponseRedirect(reverse("console:provider", args=[pk]), status=303)

    def get(self, request, *args, **kwargs):
        if self.pk is None and not self.can_change:
            return self.forbidden()
        kind = request.GET.get("provider")
        if self.pk is None and not kind:
            return self.picker()
        try:
            editor = build_provider_editor(self.pk, kind=kind, can_change=self.can_change)
        except UnknownProvider:
            return self.unknown_provider()
        return self.render_editor(editor)

    def post(self, request, *args, **kwargs):
        if not self.can_change:
            return self.forbidden()
        digest = request.POST.get("digest", "")
        shown = request.GET.get("provider")
        try:
            if "switch_provider" in request.POST:
                return self.switch(request.POST.get("provider", ""), shown, digest)
            editor = build_provider_editor(
                self.pk, request.POST, kind=shown, can_change=True, digest=digest
            )
        except UnknownProvider:
            return self.unknown_provider()
        if editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        if self.pk is None:
            return self.create(editor)
        return self.update(editor, self.pk, digest)

    def switch(self, target: str, shown: str | None, digest: str):
        """Re-render for ``target`` and save nothing.

        On the provider already shown, the posted edits stay. On another, the form opens on
        that provider's defaults and only the name, description and on box carry over.
        """
        data = self.request.POST
        if target and target == shown:
            editor = build_provider_editor(
                self.pk, data, kind=target, can_change=True, digest=digest
            )
            if editor is not None:
                editor.form.errors.clear()
            return self.render_editor(editor)
        carried = {
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "is_active": bool(data.get("is_active")),
        }
        editor = build_provider_editor(
            self.pk, kind=target, can_change=True, digest=digest, carried=carried
        )
        return self.render_editor(editor)

    def name_taken(self, editor: ProviderEditor):
        editor.form.add_error("name", NAME_TAKEN)
        return self.render_editor(editor)

    def taken_by_another(self, name: str, pk: int | None) -> bool:
        others = IntelligenceProvider.objects.filter(name=name)
        return (others if pk is None else others.exclude(pk=pk)).exists()

    def create(self, editor: ProviderEditor):
        fields = editor.form.provider_fields()
        try:
            # A savepoint, so the re-render can still read after a unique-name race.
            with transaction.atomic():
                provider = IntelligenceProvider.objects.create(**fields)
        except IntegrityError:
            if self.taken_by_another(fields["name"], None):
                return self.name_taken(editor)
            return self.render_editor(editor, Notice(NOT_ADDED, "warning"), 409)
        except OperationalError:
            return self.render_editor(editor, Notice(NOT_ADDED, "warning"), 409)
        return self.landed(ADDED, provider.pk)

    def update(self, editor: ProviderEditor, pk: int, digest: str):
        fields = editor.form.provider_fields()
        try:
            with transaction.atomic():
                save_provider(pk, fields, expected=digest)
        except IntegrityError:
            if self.taken_by_another(fields["name"], pk):
                return self.name_taken(editor)
            return self.refresh(pk, STALE)
        except StaleProvider:
            return self.refresh(pk, STALE)
        except OperationalError:
            return self.refresh(pk, BUSY)
        return self.landed(SAVED, pk)

    def refresh(self, pk: int, notice: str):
        fresh = build_provider_editor(pk, can_change=True)
        return self.render_editor(fresh, Notice(notice, "warning"), 409)
