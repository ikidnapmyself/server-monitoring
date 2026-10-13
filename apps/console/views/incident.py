from django.shortcuts import render

from apps.console.queries.incident import build_incident_detail
from apps.console.views.base import ConsoleView


class IncidentDetailView(ConsoleView):
    template_name = "console/incident.html"
    partial_template_name = "console/partials/_incident.html"

    def get(self, request, *args, **kwargs):
        incident = build_incident_detail(kwargs["pk"], full=request.GET.get("all") == "1")
        if incident is None:
            # The whole page for htmx too. It doesn't swap a 404, it shows the banner.
            return render(request, "console/not_found.html", {"nav": "incidents"}, status=404)
        return self.render_to_response(self.get_context_data(incident=incident, nav="incidents"))
