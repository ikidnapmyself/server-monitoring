from django.shortcuts import render

from apps.console.views.base import ConsoleView

NODE_READ = ("alerts.view_node", "alerts.change_node")


class NodePageView(ConsoleView):
    read_permissions = NODE_READ

    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "nodes"}, status=404)


class NodeDetailView(NodePageView):
    def get(self, request, *args, **kwargs):
        return self.not_found()
