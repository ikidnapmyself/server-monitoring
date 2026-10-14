from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from apps.alerts.services import INCIDENT_ACTIONS, IncidentManager
from apps.console.views.incident import IncidentPageView

SUMMARY_LIMIT = 2000


class IncidentActionView(IncidentPageView):
    """Acknowledge, resolve or close one incident. The rules live in ``IncidentManager``."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if not self.can_act:
            return HttpResponseForbidden("Permission to change incidents required.")
        pk, action = kwargs["pk"], kwargs["action"]
        if action not in INCIDENT_ACTIONS:
            raise Http404("Unknown incident action.")
        summary = ""
        if action == "resolve":
            summary = request.POST.get("summary", "").strip()[:SUMMARY_LIMIT]
        try:
            outcome = IncidentManager.transition(
                pk, action, actor=request.user.get_username(), summary=summary
            )
        except ObjectDoesNotExist:
            return self.not_found()
        messages.add_message(
            request, messages.SUCCESS if outcome.ok else messages.WARNING, outcome.message
        )
        full = request.POST.get("all") == "1"
        if self.wants_partial:
            return self.render_incident(pk, full=full)
        url = reverse("console:incident", args=[pk])
        return HttpResponseRedirect(f"{url}?all=1" if full else url, status=303)
