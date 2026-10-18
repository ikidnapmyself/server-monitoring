from collections.abc import Callable

from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.queries.run import build_run_detail, run_exists
from apps.console.views.base import ConsoleView, Notice
from apps.orchestration import inbox

RUN_READ = ("orchestration.view_pipelinerun", "orchestration.change_pipelinerun")
RUN_CHANGE = "orchestration.change_pipelinerun"
RETRIED = "Run queued again. The next drain runs the stages that didn't finish."
NOT_FAILED = "This run is no longer failed."
ABANDONED = "Run marked failed. No drain will pick it up."
NOT_STUCK = "This run is running or finished. Only a queued or stuck run can be marked failed."
ACTIONS: dict[str, tuple[Callable[[int], bool], str, str]] = {
    "retry": (inbox.requeue, RETRIED, NOT_FAILED),
    "abandon": (inbox.abandon, ABANDONED, NOT_STUCK),
}


class RunPageView(ConsoleView):
    """What the run page and its action view share, so both render one thing."""

    template_name = "console/run.html"
    partial_template_name = "console/partials/_run.html"
    read_permissions = RUN_READ

    @property
    def can_act(self) -> bool:
        return self.request.user.has_perm(RUN_CHANGE)

    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "runs"}, status=404)

    def render_run(self, pk: int, notice: Notice | None = None):
        run = build_run_detail(pk, can_act=self.can_act)
        if run is None:
            return self.not_found()
        return self.render_to_response(self.get_context_data(run=run, nav="runs", notice=notice))


class RunDetailView(RunPageView):
    def get(self, request, *args, **kwargs):
        return self.render_run(kwargs["pk"])


class RunActionView(RunPageView):
    """Retry or mark failed one run. The rules live in ``apps.orchestration.inbox``."""

    http_method_names = ["post"]

    def landing_path(self) -> str:
        return reverse("console:run", args=[self.kwargs["pk"]])

    def post(self, request, *args, **kwargs):
        if not self.can_act:
            return HttpResponseForbidden("Permission to change runs required.")
        if kwargs["action"] not in ACTIONS:
            raise Http404("Unknown run action.")
        pk = kwargs["pk"]
        write, won, lost = ACTIONS[kwargs["action"]]
        notice = Notice(won, "ok") if write(pk) else Notice(lost, "warning")
        if self.wants_partial:
            # Not the messages framework: the next poll, or an admin page, would eat it.
            return self.render_run(pk, notice=notice)
        if not run_exists(pk):
            return self.not_found()
        messages.add_message(
            request, messages.SUCCESS if notice.tone == "ok" else messages.WARNING, notice.text
        )
        return HttpResponseRedirect(self.landing_path(), status=303)
