from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.intents import KEY_DELETE, KEY_READ
from apps.console.queries.keys import build_key_deletion
from apps.console.views.base import ConsoleView
from config.models import APIKey


class KeyDeleteView(ConsoleView):
    """Confirm deleting one API key, saying when it was last used, then delete it."""

    template_name = "console/key_delete.html"
    partial_template_name = "console/partials/_key_delete.html"
    read_permissions = KEY_READ

    def landing_path(self) -> str:
        return reverse("console:key", args=[self.kwargs["pk"]])

    def forbidden(self):
        return HttpResponseForbidden("Permission to delete API keys required.")

    def not_found(self):
        context = {"nav": "system", "section": "keys"}
        return render(self.request, "console/not_found.html", context, status=404)

    def get(self, request, *args, **kwargs):
        if not request.user.has_perm(KEY_DELETE):
            return self.forbidden()
        deletion = build_key_deletion(kwargs["pk"])
        if deletion is None:
            return self.not_found()
        context = self.get_context_data(deletion=deletion, nav="system", section="keys")
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm(KEY_DELETE):
            return self.forbidden()
        key = APIKey.objects.filter(pk=kwargs["pk"]).first()
        if key is None:
            return self.not_found()
        key.delete()
        messages.success(request, f"API key {key.name} deleted.")
        return HttpResponseRedirect(reverse("console:keys"), status=303)
