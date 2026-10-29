from urllib.parse import urlencode

from django.contrib import messages
from django.db import OperationalError
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.alerts.node_policy import StalePolicy, save_policy, scoring_changed
from apps.console.queries.allow_ports import build_allow_ports, parse_ports
from apps.console.views.base import Notice
from apps.console.views.node import NodePageView

STALE = "This node's policy changed since you looked. Here is what allowing the ports does now."
NO_PORTS = "No ports to allow."
ALLOWED = "Allowed ports {ports} on {name}."


class NodeAllowPortsView(NodePageView):
    """Confirm, then add ports to a node's allowlist. The write is ``save_policy``."""

    template_name = "console/allow_ports.html"
    partial_template_name = "console/partials/_allow_ports.html"

    def ports(self, data) -> tuple[int, ...]:
        return parse_ports(data.get("ports", ""))

    def render_confirm(self, ports, notice: Notice | None = None, status: int = 200):
        plan = build_allow_ports(self.kwargs["pk"], ports)
        if plan is None:
            return self.not_found()
        context = self.get_context_data(plan=plan, nav="nodes", notice=notice)
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        return self.render_confirm(self.ports(request.GET))

    def post(self, request, *args, **kwargs):
        ports = self.ports(request.POST)
        plan = build_allow_ports(kwargs["pk"], ports)
        if plan is None:
            return self.not_found()
        if not ports:
            return self.render_confirm(ports, Notice(NO_PORTS, "warning"), 409)
        try:
            stored = save_policy(plan.pk, plan.config, expected=request.POST.get("digest", ""))
        except (StalePolicy, OperationalError):
            return self.render_confirm(ports, Notice(STALE, "warning"), 409)
        if scoring_changed(stored, plan.config):
            messages.info(request, "Allowed. Here is what re-evaluating the open alerts would do.")
            query = urlencode({"checker": "listening_ports"})
            return HttpResponseRedirect(
                f"{reverse('console:node-reeval', args=[plan.pk])}?{query}", status=303
            )
        messages.success(request, ALLOWED.format(ports=", ".join(map(str, ports)), name=plan.name))
        return HttpResponseRedirect(plan.back_url, status=303)
