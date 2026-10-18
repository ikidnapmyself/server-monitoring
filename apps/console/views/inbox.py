from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from apps.console.filters import InboxFilter
from apps.console.queries.inbox import build_inbox
from apps.console.views.base import ConsoleView, Notice
from apps.orchestration import inbox

INBOX_READ = ("orchestration.view_inboxitem", "orchestration.change_inboxitem")
INBOX_CHANGE = "orchestration.change_inboxitem"
DRAIN_LIMIT = 25
LEVELS = {"ok": messages.SUCCESS, "info": messages.INFO, "critical": messages.ERROR}


def _runs(count: int) -> str:
    return f"{count} run{'s' if count != 1 else ''}"


def _drained(ran: int, failed: int) -> Notice:
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
    read_permissions = INBOX_READ

    @property
    def can_drain(self) -> bool:
        return self.request.user.has_perm(INBOX_CHANGE)

    def get(self, request, *args, **kwargs):
        return self.render_inbox(InboxFilter.parse(request.GET))

    def render_inbox(self, criteria: InboxFilter, notice: Notice | None = None):
        inbox_page = build_inbox(criteria, can_drain=self.can_drain)
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
            ran, failed = inbox.drain_each([self.kwargs["pk"]])
            if not ran and not failed:
                return Notice("This run is no longer queued.", "warning")
            return _drained(ran, failed)
        if action == "drain-oldest":
            pks = inbox.pending_pks(DRAIN_LIMIT)
            if not pks:
                return Notice("Nothing was waiting.", "info")
            return _drained(*inbox.drain_each(pks))
        count = inbox.reclaim_stuck()
        if not count:
            return Notice("No run was stuck.", "info")
        plural = "s" if count != 1 else ""
        return Notice(f"Returned {count} stuck run{plural} to the queue.", "ok")

    def post(self, request, *args, **kwargs):
        if not self.can_drain:
            return HttpResponseForbidden("Permission to drain the inbox required.")
        notice = self.act()
        criteria = InboxFilter.parse(request.POST)
        if self.wants_partial:
            return self.render_inbox(criteria, notice=notice)
        messages.add_message(request, LEVELS.get(notice.tone, messages.WARNING), notice.text)
        return HttpResponseRedirect(self.landing_path() + criteria.querystring(), status=303)
