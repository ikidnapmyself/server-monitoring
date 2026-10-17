from apps.console.filters import AlertFilter
from apps.console.queries.alerts import build_alert_list
from apps.console.views.base import ConsoleView


class AlertListView(ConsoleView):
    template_name = "console/alerts.html"
    partial_template_name = "console/partials/_alerts.html"
    read_permissions = ("alerts.view_alert", "alerts.change_alert")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_alert_list(AlertFilter.parse(self.request.GET))
        context["nav"] = "alerts"
        return context
