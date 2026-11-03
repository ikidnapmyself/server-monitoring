from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.console.audit import record
from apps.console.audited import inbox_target, result, run_target
from apps.console.filters import InboxFilter
from apps.console.queries.inbox import build_inbox
from apps.console.views.base import ConsoleView, Notice
from apps.orchestration import inbox
from apps.orchestration.models import PipelineRun, PipelineStatus

DRAIN_LIMIT = 10
DRAIN_SECONDS = 60
LEVELS = {"ok": messages.SUCCESS, "info": messages.INFO, "critical": messages.ERROR}


def _runs(count: int) -> str:
    return f"{count} run{'s' if count != 1 else ''}"


def _drained(ran: int, failed: int, left: int = 0) -> Notice:
    notice = _outcome(ran, failed)
    if not left:
        return notice
    return Notice(f"{notice.text} {_runs(left)} left for the next drain.", notice.tone)


def _outcome(ran: int, failed: int) -> Notice:
    if not failed:
        return Notice(f"Ran {_runs(ran)}.", "ok")
    if ran:
        return Notice(f"Ran {_runs(ran)}. {_runs(failed)} raised an error.", "critical")
    if failed == 1:
        return Notice("1 run raised an error. Its page shows where it stopped.", "critical")
    return Notice(
        f"{_runs(failed)} raised an error. Their pages show where they stopped.", "critical"
    )


class InboxPageView(ConsoleView):
    template_name = "console/inbox.html"
    partial_template_name = "console/partials/_inbox.html"

    def get(self, request, *args, **kwargs):
        return self.render_inbox(InboxFilter.parse(request.GET))

    def render_inbox(self, criteria: InboxFilter, notice: Notice | None = None):
        inbox_page = build_inbox(criteria)
        return self.render_to_response(
            self.get_context_data(inbox=inbox_page, nav="inbox", notice=notice)
        )


class InboxActionView(InboxPageView):
    """Drain now, drain the oldest, or return stuck runs. The rules live in ``inbox``."""

    http_method_names = ["post"]

    def landing_path(self) -> str:
        return reverse("console:inbox")

    def act(self) -> Notice:
        action = self.kwargs["action"]
        if action == "drain":
            pk = self.kwargs["pk"]
            # A drain runs pipelines and commits as it goes, so it is recorded once it's done.
            ran, failed = inbox.drain_each([pk])
            if not ran and not failed:
                return Notice("This run is no longer queued.", "warning")
            record(
                self.request, "inbox.drain", run_target(pk), changes=result(ran=ran, failed=failed)
            )
            return _drained(ran, failed)
        if action == "drain-oldest":
            pks = inbox.pending_pks(DRAIN_LIMIT)
            if not pks:
                return Notice("Nothing was waiting.", "info")
            ran, failed = inbox.drain_each(pks, seconds=DRAIN_SECONDS)
            left = PipelineRun.objects.filter(status=PipelineStatus.PENDING).count()
            if ran or failed:
                changes = result(ran=ran, failed=failed, left=left)
                record(self.request, "inbox.drain_oldest", inbox_target(), changes=changes)
            return _drained(ran, failed, left)
        with transaction.atomic():
            count = inbox.reclaim_stuck()
            if count:
                changes = result(returned=count)
                record(self.request, "inbox.reclaim", inbox_target(), changes=changes)
        if not count:
            return Notice("No run was stuck.", "info")
        plural = "s" if count != 1 else ""
        return Notice(f"Returned {count} stuck run{plural} to the queue.", "ok")

    def post(self, request, *args, **kwargs):
        notice = self.act()
        criteria = InboxFilter.parse(request.POST)
        if self.wants_partial:
            return self.render_inbox(criteria, notice=notice)
        messages.add_message(request, LEVELS.get(notice.tone, messages.WARNING), notice.text)
        return HttpResponseRedirect(self.landing_path() + criteria.querystring(), status=303)
