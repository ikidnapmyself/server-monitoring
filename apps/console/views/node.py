from django.http import HttpResponseForbidden
from django.shortcuts import render

from apps.console.intents import NODE_READ
from apps.console.queries.node import build_node_detail
from apps.console.views.base import ConsoleView

NODE_CHANGE = "alerts.change_node"


class NodePageView(ConsoleView):
    read_permissions = NODE_READ

    @property
    def can_change(self) -> bool:
        return self.request.user.has_perm(NODE_CHANGE)

    def forbidden(self):
        return HttpResponseForbidden("Permission to change nodes required.")

    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "nodes"}, status=404)


class NodeDetailView(NodePageView):
    template_name = "console/node.html"
    partial_template_name = "console/partials/_node.html"

    def get(self, request, *args, **kwargs):
        node = build_node_detail(kwargs["pk"], can_change=self.can_change)
        if node is None:
            return self.not_found()
        return self.render_to_response(self.get_context_data(node=node, nav="nodes"))
