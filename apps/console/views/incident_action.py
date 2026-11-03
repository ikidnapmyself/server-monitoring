from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse

from apps.alerts.models import Incident
from apps.alerts.services import INCIDENT_ACTIONS, IncidentManager
from apps.console.audit import record
from apps.console.audited import incident_target
from apps.console.views.base import Notice
from apps.console.views.incident import IncidentPageView


class IncidentActionView(IncidentPageView):
    """Acknowledge, resolve or close one incident. The rules live in ``IncidentManager``."""

    http_method_names = ["post"]

    def landing_path(self) -> str:
        return reverse("console:incident", args=[self.kwargs["pk"]])

    def _summary(self) -> str:
        # A browser sends each line break as CRLF, which would count twice against the
        # cap. PostgreSQL refuses a NUL in text.
        text = self.request.POST.get("summary", "").replace("\r\n", "\n").replace("\x00", "")
        return text.strip()[: self.summary_limit]

    def post(self, request, *args, **kwargs):
        pk, action = kwargs["pk"], kwargs["action"]
        if action not in INCIDENT_ACTIONS:
            raise Http404("Unknown incident action.")
        summary = self._summary() if action == "resolve" else ""
        try:
            with transaction.atomic():
                outcome = IncidentManager.transition(
                    pk, action, actor=request.user.get_username(), summary=summary
                )
                if outcome.ok:
                    changes = {"summary": {"old": "", "new": summary}} if summary else {}
                    record(
                        request,
                        f"incident.{action}",
                        incident_target(pk, outcome.incident.subject),
                        changes=changes,
                    )
        except Incident.DoesNotExist:
            return self.not_found()
        full = request.POST.get("all") == "1"
        if self.wants_partial:
            # Not the messages framework: the next poll, or another page, would eat it.
            notice = Notice(text=outcome.message, tone="ok" if outcome.ok else "warning")
            return self.render_incident(pk, full=full, notice=notice)
        messages.add_message(
            request, messages.SUCCESS if outcome.ok else messages.WARNING, outcome.message
        )
        url = self.landing_path()
        return HttpResponseRedirect(f"{url}?all=1" if full else url, status=303)
