from apps.console.intents import PROVIDER_ADD, PROVIDER_READ
from apps.console.queries.providers import build_provider_list
from apps.console.views.base import ConsoleView


class ProvidersView(ConsoleView):
    template_name = "console/providers.html"
    partial_template_name = "console/partials/_providers.html"
    read_permissions = PROVIDER_READ

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_add = self.request.user.has_perm(PROVIDER_ADD)
        context["providers"] = build_provider_list(can_add=can_add)
        context["nav"] = "system"
        context["section"] = "providers"
        return context
