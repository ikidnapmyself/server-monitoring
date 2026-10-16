from apps.console.filters import IncidentFilter
from apps.console.queries.incidents import build_incident_list
from apps.console.views.base import ConsoleView


class IncidentListView(ConsoleView):
    template_name = "console/incidents.html"
    partial_template_name = "console/partials/_incidents.html"
    read_permission = "alerts.view_incident"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_incident_list(IncidentFilter.parse(self.request.GET))
        context["nav"] = "incidents"
        return context
