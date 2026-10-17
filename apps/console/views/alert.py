from django.shortcuts import render

from apps.console.queries.alert import build_alert_detail
from apps.console.views.base import ConsoleView

CHANGE_PERMISSION = "alerts.change_alert"


class AlertPageView(ConsoleView):
    read_permissions = ("alerts.view_alert", "alerts.change_alert")

    @property
    def can_reeval(self) -> bool:
        return self.request.user.has_perm(CHANGE_PERMISSION)

    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "alerts"}, status=404)


class AlertDetailView(AlertPageView):
    template_name = "console/alert.html"
    partial_template_name = "console/partials/_alert.html"

    def get(self, request, *args, **kwargs):
        alert = build_alert_detail(kwargs["pk"], can_reeval=self.can_reeval)
        if alert is None:
            return self.not_found()
        return self.render_to_response(self.get_context_data(alert=alert, nav="alerts"))
