from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.audit import record
from apps.console.queries.channels import build_channel_deletion
from apps.console.views.base import ConsoleView
from apps.console.views.channel import channel_changes, channel_state, channel_target
from apps.notify.models import NotificationChannel


class ChannelDeleteView(ConsoleView):
    """Confirm deleting one channel, naming the routes it cuts, then delete it."""

    template_name = "console/channel_delete.html"
    partial_template_name = "console/partials/_channel_delete.html"

    def landing_path(self) -> str:
        return reverse("console:channel", args=[self.kwargs["pk"]])

    def not_found(self):
        context = {"nav": "system", "section": "channels"}
        return render(self.request, "console/not_found.html", context, status=404)

    def get(self, request, *args, **kwargs):
        deletion = build_channel_deletion(kwargs["pk"])
        if deletion is None:
            return self.not_found()
        context = self.get_context_data(deletion=deletion, nav="system", section="channels")
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            channel = NotificationChannel.objects.filter(pk=kwargs["pk"]).first()
            if channel is None:
                return self.not_found()
            target, before = channel_target(channel), channel_state(channel)
            channel.delete()
            record(request, "channel.delete", target, changes=channel_changes(before, {}))
        messages.success(request, f"Channel {channel.name} deleted.")
        return HttpResponseRedirect(reverse("console:channels"), status=303)
