from apps.console.queries.keys import build_key_list
from apps.console.views.base import ConsoleView


class KeysView(ConsoleView):
    template_name = "console/keys.html"
    partial_template_name = "console/partials/_keys.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["keys"] = build_key_list()
        context["nav"] = "system"
        context["section"] = "keys"
        return context
