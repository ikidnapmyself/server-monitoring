from django.contrib import messages
from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from apps.console.queries.channels import (
    ChannelEditor,
    UnknownDriver,
    build_channel_editor,
    driver_choices,
)
from apps.console.views.base import ConsoleView, Notice
from apps.notify.channels import StaleChannel, save_channel
from apps.notify.forms import NAME_TAKEN
from apps.notify.models import NotificationChannel

STALE = "This channel changed since you opened it. Here it is now."
BUSY = "Couldn't save just now. Here's the channel as it is."
NO_SUCH_DRIVER = "This hub has no driver by that name. Pick one it has."
NOT_ADDED = "Couldn't add the channel just now. Nothing was saved, so try again."
SAVED = "Channel saved."
ADDED = "Channel added."


class ChannelView(ConsoleView):
    """Edit one channel, or add one. The rules live in ``ChannelForm`` and ``save_channel``."""

    template_name = "console/channel.html"
    partial_template_name = "console/partials/_channel.html"

    @property
    def pk(self) -> int | None:
        return self.kwargs.get("pk")

    def landing_path(self) -> str:
        if self.pk is None:
            return reverse("console:channel-new")
        return reverse("console:channel", args=[self.pk])

    def not_found(self):
        context = {"nav": "system", "section": "channels"}
        return render(self.request, "console/not_found.html", context, status=404)

    def picker(self, notice: Notice | None = None, status: int = 200):
        context = self.get_context_data(
            drivers=driver_choices(), nav="system", section="channels", notice=notice
        )
        return render(self.request, "console/channel_new.html", context, status=status)

    def render_editor(
        self, editor: ChannelEditor | None, notice: Notice | None = None, status: int = 200
    ):
        if editor is None:
            return self.not_found()
        context = self.get_context_data(
            editor=editor, nav="system", section="channels", notice=notice
        )
        return self.render_to_response(context, status=status)

    def unknown_driver(self):
        notice = Notice(NO_SUCH_DRIVER, "warning")
        if self.pk is None:
            return self.picker(notice, 400)
        editor = build_channel_editor(self.pk)
        return self.render_editor(editor, notice, 400)

    def landed(self, message: str, pk: int):
        messages.success(self.request, message)
        return HttpResponseRedirect(reverse("console:channel", args=[pk]), status=303)

    def get(self, request, *args, **kwargs):
        driver = request.GET.get("driver")
        if self.pk is None and not driver:
            return self.picker()
        try:
            editor = build_channel_editor(self.pk, driver=driver)
        except UnknownDriver:
            return self.unknown_driver()
        return self.render_editor(editor)

    def post(self, request, *args, **kwargs):
        digest = request.POST.get("digest", "")
        shown = request.GET.get("driver")
        try:
            if "switch_driver" in request.POST:
                return self.switch(request.POST.get("driver", ""), shown, digest)
            editor = build_channel_editor(self.pk, request.POST, driver=shown, digest=digest)
        except UnknownDriver:
            return self.unknown_driver()
        if editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        if self.pk is None:
            return self.create(editor)
        return self.update(editor, self.pk, digest)

    def switch(self, target: str, shown: str | None, digest: str):
        """Re-render for ``target`` and save nothing.

        On the driver already shown, the posted edits stay. On another, the form opens on that
        driver's defaults and only the name, description and active box carry over.
        """
        data = self.request.POST
        if target and target == shown:
            editor = build_channel_editor(self.pk, data, driver=target, digest=digest)
            if editor is not None:
                editor.form.errors.clear()
            return self.render_editor(editor)
        carried = {
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "is_active": bool(data.get("is_active")),
        }
        editor = build_channel_editor(self.pk, driver=target, digest=digest, carried=carried)
        return self.render_editor(editor)

    def name_taken(self, editor: ChannelEditor):
        editor.form.add_error("name", NAME_TAKEN)
        return self.render_editor(editor)

    def taken_by_another(self, name: str, pk: int | None) -> bool:
        others = NotificationChannel.objects.filter(name=name)
        return (others if pk is None else others.exclude(pk=pk)).exists()

    def create(self, editor: ChannelEditor):
        fields = editor.form.channel_fields()
        try:
            # A savepoint, so the re-render can still read after a unique-name race.
            with transaction.atomic():
                channel = NotificationChannel.objects.create(**fields)
        except IntegrityError:
            if self.taken_by_another(fields["name"], None):
                return self.name_taken(editor)
            return self.render_editor(editor, Notice(NOT_ADDED, "warning"), 409)
        except OperationalError:
            return self.render_editor(editor, Notice(NOT_ADDED, "warning"), 409)
        return self.landed(ADDED, channel.pk)

    def update(self, editor: ChannelEditor, pk: int, digest: str):
        fields = editor.form.channel_fields()
        try:
            with transaction.atomic():
                save_channel(pk, fields, expected=digest)
        except IntegrityError:
            if self.taken_by_another(fields["name"], pk):
                return self.name_taken(editor)
            return self.refresh(pk, STALE)
        except StaleChannel:
            return self.refresh(pk, STALE)
        except OperationalError:
            return self.refresh(pk, BUSY)
        return self.landed(SAVED, pk)

    def refresh(self, pk: int, notice: str):
        fresh = build_channel_editor(pk)
        return self.render_editor(fresh, Notice(notice, "warning"), 409)
