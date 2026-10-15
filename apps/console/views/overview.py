from apps.console.queries.overview import build_overview
from apps.console.views.base import ConsoleView


class OverviewView(ConsoleView):
    template_name = "console/overview.html"
    partial_template_name = "console/partials/_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        overview = build_overview()
        context["overview"] = overview
        context["nav"] = "overview"
        return context
