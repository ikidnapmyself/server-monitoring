from apps.console.views.base import ConsoleView


class OverviewView(ConsoleView):
    template_name = "console/overview.html"
    partial_template_name = "console/partials/_overview.html"
