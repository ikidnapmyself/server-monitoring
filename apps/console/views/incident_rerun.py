from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from apps.alerts.check_integration import rerun_incident_checks
from apps.alerts.models import Incident
from apps.console.views.incident import IncidentPageView, Notice

REFUSED = "This incident's checks can't run on this machine."
RAN = "Checks ran again. The incident updates when the run finishes."


class IncidentRerunView(IncidentPageView):
    """Run an incident's checkers again on this machine. The rules live in ``apps.alerts``."""

    http_method_names = ["post"]

    def landing_path(self) -> str:
        return reverse("console:incident", args=[self.kwargs["pk"]])

    def post(self, request, *args, **kwargs):
        if not self.can_rerun_checks:
            return HttpResponseForbidden("Permission to run checks required.")
        pk = kwargs["pk"]
        try:
            rerun_incident_checks(pk)
        except Incident.DoesNotExist:
            return self.not_found()
        except ValueError:
            notice = Notice(text=REFUSED, tone="warning")
        else:
            notice = Notice(text=RAN, tone="ok")
        full = request.POST.get("all") == "1"
        if self.wants_partial:
            return self.render_incident(pk, full=full, notice=notice)
        messages.add_message(
            request, messages.SUCCESS if notice.tone == "ok" else messages.WARNING, notice.text
        )
        url = self.landing_path()
        return HttpResponseRedirect(f"{url}?all=1" if full else url, status=303)
