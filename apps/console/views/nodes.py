from apps.console.filters import NodeFilter
from apps.console.queries.nodes import build_node_list
from apps.console.views.node import NodePageView


class NodeListView(NodePageView):
    template_name = "console/nodes.html"
    partial_template_name = "console/partials/_nodes.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list"] = build_node_list(NodeFilter.parse(self.request.GET))
        context["nav"] = "nodes"
        return context
