from apps.console.intents import ROUTE_READ
from apps.console.queries.routes import build_route_list
from apps.console.views.base import ConsoleView


class RoutesView(ConsoleView):
    template_name = "console/routes.html"
    partial_template_name = "console/partials/_routes.html"
    read_permissions = ROUTE_READ

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["routes"] = build_route_list()
        context["nav"] = "system"
        context["section"] = "routes"
        return context
