from apps.console.filters import PreflightFilter
from apps.console.queries.preflight import build_preflight_list
from apps.console.views.base import ConsoleView


class PreflightListView(ConsoleView):
    template_name = "console/preflight.html"
    partial_template_name = "console/partials/_preflight.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_preflight_list(PreflightFilter.parse(self.request.GET))
        context["nav"] = "reports"
        context["section"] = "preflight"
        return context
