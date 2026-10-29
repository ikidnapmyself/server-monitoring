from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.queries.routes import build_route_deletion
from apps.console.views.base import ConsoleView
from apps.orchestration.models import PipelineDefinition


class RouteDeleteView(ConsoleView):
    """Confirm deleting one route, saying where its traffic goes, then delete it."""

    template_name = "console/route_delete.html"
    partial_template_name = "console/partials/_route_delete.html"

    def landing_path(self) -> str:
        return reverse("console:route", args=[self.kwargs["pk"]])

    def not_found(self):
        context = {"nav": "system", "section": "routes"}
        return render(self.request, "console/not_found.html", context, status=404)

    def get(self, request, *args, **kwargs):
        deletion = build_route_deletion(kwargs["pk"])
        if deletion is None:
            return self.not_found()
        context = self.get_context_data(deletion=deletion, nav="system", section="routes")
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        lane = PipelineDefinition.objects.filter(pk=kwargs["pk"]).first()
        if lane is None:
            return self.not_found()
        lane.delete()
        messages.success(request, f"Route {lane.name} deleted.")
        return HttpResponseRedirect(reverse("console:routes"), status=303)
