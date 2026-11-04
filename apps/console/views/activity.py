from apps.console.filters import ActivityFilter
from apps.console.queries.activity import build_activity_list
from apps.console.views.base import ConsoleView


class ActivityView(ConsoleView):
    """Who changed what in the console, newest first."""

    template_name = "console/activity.html"
    partial_template_name = "console/partials/_activity.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_activity_list(ActivityFilter.parse(self.request.GET))
        context["nav"] = "system"
        context["section"] = "activity"
        return context
