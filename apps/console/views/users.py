from apps.console.queries.users import build_user_list
from apps.console.views.base import ConsoleView


class UsersView(ConsoleView):
    template_name = "console/users.html"
    partial_template_name = "console/partials/_users.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["users"] = build_user_list()
        context["nav"] = "system"
        context["section"] = "users"
        return context
