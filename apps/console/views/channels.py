from apps.console.filters import SEARCH_LIMIT
from apps.console.queries.channels import build_channel_list
from apps.console.views.base import ConsoleView


class ChannelsView(ConsoleView):
    template_name = "console/channels.html"
    partial_template_name = "console/partials/_channels.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # PostgreSQL refuses a NUL in text.
        q = self.request.GET.get("q", "").replace("\x00", "").strip()[:SEARCH_LIMIT]
        context["channels"] = build_channel_list(q)
        context["nav"] = "system"
        context["section"] = "channels"
        return context
