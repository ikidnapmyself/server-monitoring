from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.urls import reverse
from django.views.generic import TemplateView


class ConsoleView(TemplateView):
    """Base for every console page. Staff only."""

    def dispatch(self, request, *args, **kwargs):
        return self._refusal(request) or super().dispatch(request, *args, **kwargs)

    def _refusal(self, request):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("admin:login"))
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access required.")
        return None
