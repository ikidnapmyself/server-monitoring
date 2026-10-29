from django.shortcuts import render

from apps.console.queries.incident import build_incident_detail
from apps.console.views.base import ConsoleView, Notice


class IncidentPageView(ConsoleView):
    """What the incident page and its action view share, so both render one thing."""

    template_name = "console/incident.html"
    partial_template_name = "console/partials/_incident.html"
    summary_limit = 2000

    def not_found(self):
        # The whole page for htmx too. It doesn't swap a 404, it shows the banner.
        return render(self.request, "console/not_found.html", {"nav": "incidents"}, status=404)

    def render_incident(self, pk: int, *, full: bool, notice: Notice | None = None):
        incident = build_incident_detail(pk, full=full)
        if incident is None:
            return self.not_found()
        return self.render_to_response(
            self.get_context_data(
                incident=incident,
                nav="incidents",
                notice=notice,
                summary_limit=self.summary_limit,
            )
        )


class IncidentDetailView(IncidentPageView):
    def get(self, request, *args, **kwargs):
        return self.render_incident(kwargs["pk"], full=request.GET.get("all") == "1")
