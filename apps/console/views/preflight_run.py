from django.shortcuts import render

from apps.console.queries.preflight_run import build_preflight_detail
from apps.console.views.base import ConsoleView

NAV = {"nav": "reports", "section": "preflight"}


class PreflightRunView(ConsoleView):
    template_name = "console/preflight_run.html"
    partial_template_name = "console/partials/_preflight_run.html"

    def get(self, request, *args, **kwargs):
        run = build_preflight_detail(kwargs["pk"])
        if run is None:
            return render(request, "console/not_found.html", NAV, status=404)
        return self.render_to_response(self.get_context_data(run=run, **NAV))
