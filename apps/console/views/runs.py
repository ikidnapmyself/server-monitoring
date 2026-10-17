from apps.console.filters import RunFilter
from apps.console.queries.runs import build_run_list
from apps.console.views.base import ConsoleView
from apps.console.views.run import RUN_READ


class RunListView(ConsoleView):
    template_name = "console/runs.html"
    partial_template_name = "console/partials/_runs.html"
    read_permissions = RUN_READ

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_run_list(RunFilter.parse(self.request.GET))
        context["nav"] = "runs"
        return context
