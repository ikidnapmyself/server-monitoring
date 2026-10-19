from apps.console.intents import ROUTE_ADD, ROUTE_READ
from apps.console.queries.routes import build_route_list
from apps.console.views.base import ConsoleView


class RoutesView(ConsoleView):
    template_name = "console/routes.html"
    partial_template_name = "console/partials/_routes.html"
    read_permissions = ROUTE_READ

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_add = self.request.user.has_perm(ROUTE_ADD)
        context["routes"] = build_route_list(can_add=can_add)
        context["nav"] = "system"
        context["section"] = "routes"
        return context
