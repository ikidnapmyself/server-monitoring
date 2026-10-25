from django.shortcuts import render

from apps.console.intents import ANALYSIS_READ
from apps.console.queries.analysis import build_analysis_detail
from apps.console.views.base import ConsoleView

NAV = {"nav": "reports", "section": "analyses"}


class AnalysisDetailView(ConsoleView):
    template_name = "console/analysis.html"
    partial_template_name = "console/partials/_analysis.html"
    read_permissions = ANALYSIS_READ

    def get(self, request, *args, **kwargs):
        analysis = build_analysis_detail(kwargs["pk"])
        if analysis is None:
            return render(request, "console/not_found.html", NAV, status=404)
        return self.render_to_response(self.get_context_data(analysis=analysis, **NAV))
