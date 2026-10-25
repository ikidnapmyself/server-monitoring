from django.utils import timezone

from apps.console.intents import ANALYSIS_READ, CHECK_READ, PREFLIGHT_READ, RUN_READ
from apps.console.queries.reports import build_reports
from apps.console.views.base import ConsoleView


class ReportsView(ConsoleView):
    template_name = "console/reports.html"
    partial_template_name = "console/partials/_reports.html"
    read_permissions = (*RUN_READ, *CHECK_READ, *PREFLIGHT_READ, *ANALYSIS_READ)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["reports"] = build_reports(self.request.user, timezone.now())
        context["nav"] = "reports"
        context["section"] = "summary"
        return context
