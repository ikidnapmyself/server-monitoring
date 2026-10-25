from apps.console.filters import AnalysisFilter
from apps.console.intents import ANALYSIS_READ
from apps.console.queries.analyses import build_analysis_list
from apps.console.views.base import ConsoleView


class AnalysisListView(ConsoleView):
    template_name = "console/analyses.html"
    partial_template_name = "console/partials/_analyses.html"
    read_permissions = ANALYSIS_READ

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_analysis_list(AnalysisFilter.parse(self.request.GET))
        context["nav"] = "reports"
        context["section"] = "analyses"
        return context
