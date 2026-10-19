from django.contrib import messages
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.intents import ROUTE_ADD, ROUTE_CHANGE, ROUTE_READ
from apps.console.queries.routes import RouteEditor, build_route_editor
from apps.console.views.base import ConsoleView, Notice
from apps.orchestration.forms import RouteForm
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.routing import StaleRoute, save_route

STALE = "This route changed since you opened it. Here it is now."
NAME_TAKEN = "Another route already has this name."
SAVED = "Route saved."
ADDED = "Route added."


class RouteView(ConsoleView):
    """Edit one route, or add one. The rules live in ``RouteForm`` and ``save_route``."""

    template_name = "console/route.html"
    partial_template_name = "console/partials/_route.html"
    read_permissions = ROUTE_READ

    @property
    def pk(self) -> int | None:
        return self.kwargs.get("pk")

    @property
    def can_change(self) -> bool:
        return self.request.user.has_perm(ROUTE_ADD if self.pk is None else ROUTE_CHANGE)

    def landing_path(self) -> str:
        if self.pk is None:
            return reverse("console:route-new")
        return reverse("console:route", args=[self.pk])

    def forbidden(self):
        return HttpResponseForbidden("Permission to change routes required.")

    def not_found(self):
        context = {"nav": "system", "section": "routes"}
        return render(self.request, "console/not_found.html", context, status=404)

    def render_editor(
        self, editor: RouteEditor | None, notice: Notice | None = None, status: int = 200
    ):
        if editor is None:
            return self.not_found()
        context = self.get_context_data(
            editor=editor, nav="system", section="routes", notice=notice
        )
        return self.render_to_response(context, status=status)

    def landed(self, message: str, pk: int):
        messages.success(self.request, message)
        return HttpResponseRedirect(f"{reverse('console:routes')}#route-{pk}", status=303)

    def get(self, request, *args, **kwargs):
        if self.pk is None and not self.can_change:
            return self.forbidden()
        return self.render_editor(build_route_editor(self.pk, can_change=self.can_change))

    def post(self, request, *args, **kwargs):
        if not self.can_change:
            return self.forbidden()
        pk, version = self.pk, request.POST.get("version", "")
        adding = "add_condition" in request.POST
        data = RouteForm.add_condition(request.POST) if adding else request.POST
        editor = build_route_editor(pk, data, can_change=True, version=version)
        if adding or editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        if pk is None:
            return self.create(editor)
        return self.update(editor, pk, version)

    def name_taken(self, editor: RouteEditor):
        editor.form.add_error("name", NAME_TAKEN)
        return self.render_editor(editor)

    def create(self, editor: RouteEditor):
        fields = editor.form.route_fields()
        try:
            # A savepoint, so the re-render can still read after a unique-name race.
            with transaction.atomic():
                route = PipelineDefinition.objects.create(
                    **fields, created_by=self.request.user.get_username(), version=1
                )
        except IntegrityError:
            return self.name_taken(editor)
        return self.landed(ADDED, route.pk)

    def update(self, editor: RouteEditor, pk: int, version: str):
        fields = editor.form.route_fields()
        try:
            with transaction.atomic():
                save_route(pk, fields, expected=int(version) if version.isdigit() else 0)
        except IntegrityError:
            return self.name_taken(editor)
        except (StaleRoute, OperationalError):
            fresh = build_route_editor(pk, can_change=True)
            return self.render_editor(fresh, Notice(STALE, "warning"), 409)
        return self.landed(SAVED, pk)
