from django.contrib import messages
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.queries.routes import RouteEditor, build_route_editor
from apps.console.views.base import ConsoleView, Notice
from apps.orchestration.forms import NAME_TAKEN, RouteForm
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.routing import StaleRoute, save_route

STALE = "This route changed since you opened it. Here it is now."
BUSY = "Couldn't save just now. Here's the route as it is."
SAVED = "Route saved."
ADDED = "Route added."


class RouteView(ConsoleView):
    """Edit one route, or add one. The rules live in ``RouteForm`` and ``save_route``."""

    template_name = "console/route.html"
    partial_template_name = "console/partials/_route.html"

    @property
    def pk(self) -> int | None:
        return self.kwargs.get("pk")

    def landing_path(self) -> str:
        if self.pk is None:
            return reverse("console:route-new")
        return reverse("console:route", args=[self.pk])

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
        return self.render_editor(build_route_editor(self.pk))

    def post(self, request, *args, **kwargs):
        pk, version = self.pk, request.POST.get("version", "")
        adding = "add_condition" in request.POST
        data = RouteForm.add_condition(request.POST) if adding else request.POST
        editor = build_route_editor(pk, data, version=version)
        if adding and editor is not None:
            editor.form.quiet()
            return self.render_editor(editor)
        if editor is None or not editor.form.is_valid():
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
            others = PipelineDefinition.objects.exclude(pk=pk)
            if others.filter(name=fields["name"]).exists():
                return self.name_taken(editor)
            return self.refresh(pk, STALE)
        except StaleRoute:
            return self.refresh(pk, STALE)
        except OperationalError:
            return self.refresh(pk, BUSY)
        return self.landed(SAVED, pk)

    def refresh(self, pk: int, notice: str):
        fresh = build_route_editor(pk)
        return self.render_editor(fresh, Notice(notice, "warning"), 409)
