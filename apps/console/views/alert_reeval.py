from django.contrib import messages
from django.db import OperationalError
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from apps.alerts.describing import describe_reeval_applied
from apps.alerts.models import Alert
from apps.alerts.reeval_existing import ReevalScope, StaleReeval, apply_reeval
from apps.console.queries.alert import build_reeval_preview
from apps.console.views.alert import AlertPageView
from apps.console.views.incident import Notice

STALE = "This alert changed since you looked. Here is what re-evaluating does now."


class AlertReevalView(AlertPageView):
    """Preview, then apply, one alert's re-score. The rules live in ``apply_reeval``."""

    template_name = "console/alert_reeval.html"
    partial_template_name = "console/partials/_alert_reeval.html"

    def landing_path(self) -> str:
        return self.request.path

    def render_preview(self, pk: int, notice: Notice | None = None, status: int = 200):
        preview = build_reeval_preview(pk)
        if preview is None:
            return self.not_found()
        context = self.get_context_data(
            preview=preview, can_reeval=self.can_reeval, nav="alerts", notice=notice
        )
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        return self.render_preview(kwargs["pk"])

    def post(self, request, *args, **kwargs):
        if not self.can_reeval:
            return HttpResponseForbidden("Permission to change alerts required.")
        alert = Alert.objects.filter(pk=kwargs["pk"]).first()
        if alert is None:
            return self.not_found()
        try:
            report = apply_reeval(
                ReevalScope.for_alert(alert), expected=request.POST.get("digest", "")
            )
        except (StaleReeval, OperationalError):
            return self.render_preview(alert.pk, Notice(STALE, "warning"), status=409)
        messages.success(request, describe_reeval_applied(report))
        return HttpResponseRedirect(reverse("console:alert", args=[alert.pk]), status=303)
