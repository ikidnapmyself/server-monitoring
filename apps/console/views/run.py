from django.shortcuts import render

from apps.console.queries.run import build_run_detail
from apps.console.views.base import ConsoleView, Notice

RUN_READ = ("orchestration.view_pipelinerun", "orchestration.change_pipelinerun")
RUN_CHANGE = "orchestration.change_pipelinerun"


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
