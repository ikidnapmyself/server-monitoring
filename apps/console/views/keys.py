from apps.console.intents import KEY_ADD, KEY_READ
from apps.console.queries.keys import build_key_list
from apps.console.views.base import ConsoleView


class KeysView(ConsoleView):
    template_name = "console/keys.html"
    partial_template_name = "console/partials/_keys.html"
    read_permissions = KEY_READ

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["keys"] = build_key_list(can_add=self.request.user.has_perm(KEY_ADD))
        context["nav"] = "system"
        context["section"] = "keys"
        return context
