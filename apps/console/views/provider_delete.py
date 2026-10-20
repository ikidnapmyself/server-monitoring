from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.intents import PROVIDER_DELETE, PROVIDER_READ
from apps.console.queries.providers import build_provider_deletion
from apps.console.views.base import ConsoleView
from apps.intelligence.models import IntelligenceProvider


class ProviderDeleteView(ConsoleView):
    """Confirm deleting one provider, saying what analysis uses after, then delete it."""

    template_name = "console/provider_delete.html"
    partial_template_name = "console/partials/_provider_delete.html"
    read_permissions = PROVIDER_READ

    def landing_path(self) -> str:
        return reverse("console:provider", args=[self.kwargs["pk"]])

    def forbidden(self):
        return HttpResponseForbidden("Permission to delete providers required.")

    def not_found(self):
        context = {"nav": "system", "section": "providers"}
        return render(self.request, "console/not_found.html", context, status=404)

    def get(self, request, *args, **kwargs):
        if not request.user.has_perm(PROVIDER_DELETE):
            return self.forbidden()
        deletion = build_provider_deletion(kwargs["pk"])
        if deletion is None:
            return self.not_found()
        context = self.get_context_data(deletion=deletion, nav="system", section="providers")
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm(PROVIDER_DELETE):
            return self.forbidden()
        provider = IntelligenceProvider.objects.filter(pk=kwargs["pk"]).first()
        if provider is None:
            return self.not_found()
        provider.delete()
        messages.success(request, f"Provider {provider.name} deleted.")
        return HttpResponseRedirect(reverse("console:providers"), status=303)
