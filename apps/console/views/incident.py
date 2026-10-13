from django.http import Http404

from apps.console.queries.incident import build_incident_detail
from apps.console.views.base import ConsoleView


class IncidentDetailView(ConsoleView):
    template_name = "console/incident.html"
    partial_template_name = "console/partials/_incident.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        incident = build_incident_detail(self.kwargs["pk"])
        if incident is None:
            raise Http404("No such incident.")
        context["incident"] = incident
        context["nav"] = "incidents"
        return context
