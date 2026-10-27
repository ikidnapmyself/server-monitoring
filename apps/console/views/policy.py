from apps.console.queries.policy import build_policy_page
from apps.console.views.base import ConsoleView


class PolicyView(ConsoleView):
    template_name = "console/policy.html"
    partial_template_name = "console/partials/_policy.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page"] = build_policy_page()
        context["nav"] = "nodes"
        return context
