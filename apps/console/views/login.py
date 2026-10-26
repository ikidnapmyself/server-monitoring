from django.contrib.auth.views import LoginView

from apps.console.forms import ConsoleLoginForm


class ConsoleLoginView(LoginView):
    """Django's own login view, so it isn't a ``ConsoleView``: anyone may open it."""

    template_name = "console/login.html"
    authentication_form = ConsoleLoginForm
