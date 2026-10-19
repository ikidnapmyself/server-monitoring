from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.intents import CHANNEL_DELETE, CHANNEL_READ
from apps.console.queries.channels import build_channel_deletion
from apps.console.views.base import ConsoleView
from apps.notify.models import NotificationChannel


class ChannelDeleteView(ConsoleView):
    """Confirm deleting one channel, naming the routes it cuts, then delete it."""

    template_name = "console/channel_delete.html"
    partial_template_name = "console/partials/_channel_delete.html"
    read_permissions = CHANNEL_READ

    def landing_path(self) -> str:
        return reverse("console:channel", args=[self.kwargs["pk"]])

    def forbidden(self):
        return HttpResponseForbidden("Permission to delete channels required.")

    def not_found(self):
        context = {"nav": "system", "section": "channels"}
        return render(self.request, "console/not_found.html", context, status=404)

    def get(self, request, *args, **kwargs):
        if not request.user.has_perm(CHANNEL_DELETE):
            return self.forbidden()
        deletion = build_channel_deletion(kwargs["pk"])
        if deletion is None:
            return self.not_found()
        context = self.get_context_data(deletion=deletion, nav="system", section="channels")
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm(CHANNEL_DELETE):
            return self.forbidden()
        channel = NotificationChannel.objects.filter(pk=kwargs["pk"]).first()
        if channel is None:
            return self.not_found()
        channel.delete()
        messages.success(request, f"Channel {channel.name} deleted.")
        return HttpResponseRedirect(reverse("console:channels"), status=303)
