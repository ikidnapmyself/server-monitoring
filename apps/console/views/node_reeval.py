from urllib.parse import urlencode

from django.contrib import messages
from django.db import OperationalError
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.alerts.describing import describe_reeval_applied
from apps.alerts.models import Node
from apps.alerts.reeval_existing import StaleReeval, apply_reeval
from apps.console.filters import checker_param
from apps.console.queries.reeval import build_node_reeval_preview, node_scope
from apps.console.views.base import NOTHING_TO_APPLY, Notice
from apps.console.views.node import NodePageView

STALE = (
    "This node's alerts or policy changed since you looked. Here is what re-evaluating does now."
)


class NodeReevalView(NodePageView):
    """Preview, then apply, a node's re-score. The rules live in ``apply_reeval``."""

    template_name = "console/reeval.html"
    partial_template_name = "console/partials/_reeval.html"

    @property
    def checker(self) -> str | None:
        return checker_param(self.request.GET)

    def landing_path(self) -> str:
        url = reverse("console:node-reeval", args=[self.kwargs["pk"]])
        raw = self.request.GET.get("checker")
        return f"{url}?{urlencode({'checker': raw})}" if raw else url

    def render_preview(self, notice: Notice | None = None, status: int = 200):
        preview = build_node_reeval_preview(self.kwargs["pk"], self.checker)
        if preview is None:
            return self.not_found()
        context = self.get_context_data(
            preview=preview, can_reeval=self.can_change, nav="nodes", notice=notice
        )
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        return self.render_preview()

    def post(self, request, *args, **kwargs):
        if not self.can_change:
            return self.forbidden()
        node = Node.objects.filter(pk=kwargs["pk"]).first()
        if node is None:
            return self.not_found()
        try:
            report = apply_reeval(
                node_scope(node, self.checker), expected=request.POST.get("digest", "")
            )
        except (StaleReeval, OperationalError):
            return self.render_preview(Notice(STALE, "warning"), status=409)
        if not report.changes:
            return self.render_preview(Notice(NOTHING_TO_APPLY, "info"), status=409)
        messages.success(request, describe_reeval_applied(report))
        return HttpResponseRedirect(reverse("console:node", args=[node.pk]), status=303)
