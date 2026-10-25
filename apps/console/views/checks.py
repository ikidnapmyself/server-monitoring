from apps.console.filters import CheckFilter
from apps.console.intents import CHECK_READ
from apps.console.queries.checks import build_check_list
from apps.console.views.base import ConsoleView


class CheckListView(ConsoleView):
    template_name = "console/checks.html"
    partial_template_name = "console/partials/_checks.html"
    read_permissions = CHECK_READ

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_check_list(CheckFilter.parse(self.request.GET))
        context["nav"] = "reports"
        context["section"] = "checks"
        return context
