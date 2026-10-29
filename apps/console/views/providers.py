from apps.console.queries.providers import build_provider_list
from apps.console.views.base import ConsoleView


class ProvidersView(ConsoleView):
    template_name = "console/providers.html"
    partial_template_name = "console/partials/_providers.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["providers"] = build_provider_list()
        context["nav"] = "system"
        context["section"] = "providers"
        return context
