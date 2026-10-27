from django.shortcuts import render

from apps.console.queries.node import build_node_detail
from apps.console.views.base import ConsoleView


class NodePageView(ConsoleView):
    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "nodes"}, status=404)


class NodeDetailView(NodePageView):
    template_name = "console/node.html"
    partial_template_name = "console/partials/_node.html"

    def get(self, request, *args, **kwargs):
        node = build_node_detail(kwargs["pk"])
        if node is None:
            return self.not_found()
        return self.render_to_response(self.get_context_data(node=node, nav="nodes"))
