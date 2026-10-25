from django.shortcuts import render

from apps.console.intents import CHECK_READ
from apps.console.queries.check import build_check_detail
from apps.console.views.base import ConsoleView

NAV = {"nav": "reports", "section": "checks"}


class CheckDetailView(ConsoleView):
    template_name = "console/check.html"
    partial_template_name = "console/partials/_check.html"
    read_permissions = CHECK_READ

    def get(self, request, *args, **kwargs):
        check = build_check_detail(kwargs["pk"])
        if check is None:
            return render(request, "console/not_found.html", NAV, status=404)
        return self.render_to_response(self.get_context_data(check=check, **NAV))
