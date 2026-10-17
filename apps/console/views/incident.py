from django.shortcuts import render

from apps.console.queries.incident import build_incident_detail
from apps.console.views.base import ConsoleView, Notice

CHANGE_PERMISSION = "alerts.change_incident"
RERUN_PERMISSION = "checkers.add_checkrun"


class IncidentPageView(ConsoleView):
    """What the incident page and its action view share, so both render one thing."""

    template_name = "console/incident.html"
    partial_template_name = "console/partials/_incident.html"
    read_permissions = ("alerts.view_incident", "alerts.change_incident")
    summary_limit = 2000

    @property
    def can_act(self) -> bool:
        return self.request.user.has_perm(CHANGE_PERMISSION)

    @property
    def can_rerun_checks(self) -> bool:
        return self.request.user.has_perm(RERUN_PERMISSION)

    def not_found(self):
        # The whole page for htmx too. It doesn't swap a 404, it shows the banner.
        return render(self.request, "console/not_found.html", {"nav": "incidents"}, status=404)

    def render_incident(self, pk: int, *, full: bool, notice: Notice | None = None):
        incident = build_incident_detail(
            pk, full=full, can_act=self.can_act, can_rerun=self.can_rerun_checks
        )
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
