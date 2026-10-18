from apps.console.views.node import NodePageView


class NodeReevalView(NodePageView):
    def get(self, request, *args, **kwargs):
        return self.not_found()
