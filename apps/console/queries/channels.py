"""Read models for the channels page and for one channel's editor."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from apps.notify.channels import FIELDS, channel_digest, lanes_cut_by_delete
from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.forms import UNKNOWN_DRIVER, ChannelForm
from apps.notify.models import NotificationChannel
from apps.orchestration.describing import REASONS
from apps.orchestration.models import PipelineDefinition
from config.describing import Action, Fact


class UnknownDriver(Exception):
    """A driver key that is neither registered nor the channel's own."""


@dataclass(frozen=True)
class LaneLine:
    name: str
    gap_words: str
    action: Action


@dataclass(frozen=True)
class ChannelRow:
    pk: int
    name: str
    driver_label: str
    active: bool
    lanes: tuple[LaneLine, ...]
    edit: Action

    @property
    def anchor(self) -> str:
        return f"channel-{self.pk}"


@dataclass(frozen=True)
class ChannelList:
    rows: tuple[ChannelRow, ...]
    q: str
    can_add: bool = False

    @property
    def add(self) -> Action:
        return Action("Add a channel", "add_channel", {})


@dataclass(frozen=True)
class DriverChoice:
    key: str
    label: str
    url: str


@dataclass(frozen=True)
class ChannelEditor:
    pk: int | None
    name: str
    form: ChannelForm
    digest: str
    url: str
    back_url: str
    lanes: tuple[LaneLine, ...]
    facts: tuple[Fact, ...]
    can_change: bool

    @property
    def delete(self) -> Action | None:
        if self.pk is None:
            return None
        return Action("Delete channel", "delete_channel", {"pk": self.pk})


@dataclass(frozen=True)
class ChannelDeletion:
    pk: int
    name: str
    cut: tuple[Action, ...]
    url: str
    back_url: str


def build_channel_deletion(pk: int) -> ChannelDeletion | None:
    channel = NotificationChannel.objects.filter(pk=pk).first()
    if channel is None:
        return None
    return ChannelDeletion(
        pk=channel.pk,
        name=channel.name,
        cut=tuple(
            Action(lane.name, "view_route", {"pk": lane.pk})
            for lane in lanes_cut_by_delete(channel)
        ),
        url=reverse("console:channel-delete", args=[channel.pk]),
        back_url=reverse("console:channel", args=[channel.pk]),
    )


def _label(driver: str) -> str:
    driver_cls = DRIVER_REGISTRY.get(driver)
    return driver_cls.label if driver_cls else UNKNOWN_DRIVER


def _line(lane: PipelineDefinition) -> LaneLine:
    gap = lane.delivery_gap()
    return LaneLine(
        name=lane.name,
        gap_words=REASONS[gap].headline if gap else "",
        action=Action(lane.name, "view_route", {"pk": lane.pk}),
    )


def _lanes(channels) -> dict[int | None, list[LaneLine]]:
    lanes = (
        PipelineDefinition.objects.filter(channel__in=channels)
        .select_related("channel")
        .order_by("priority", "id")
    )
    by_channel: dict[int | None, list[LaneLine]] = {}
    for lane in lanes:
        by_channel.setdefault(lane.channel_id, []).append(_line(lane))
    return by_channel


def build_channel_list(q: str, *, can_add: bool = False) -> ChannelList:
    channels = list(NotificationChannel.objects.filter(name__icontains=q).order_by("name"))
    lanes = _lanes(channels)
    rows = tuple(
        ChannelRow(
            pk=channel.pk,
            name=channel.name,
            driver_label=_label(channel.driver),
            active=channel.is_active,
            lanes=tuple(lanes.get(channel.pk, ())),
            edit=Action(channel.name, "view_channel", {"pk": channel.pk}),
        )
        for channel in channels
    )
    return ChannelList(rows=rows, q=q, can_add=can_add)


def new_channel_url(driver: str) -> str:
    return f"{reverse('console:channel-new')}?{urlencode({'driver': driver})}"


def driver_choices() -> tuple[DriverChoice, ...]:
    return tuple(
        DriverChoice(key, driver_cls.label, new_channel_url(key))
        for key, driver_cls in DRIVER_REGISTRY.items()
    )


def _when(moment) -> str:
    return date_format(timezone.localtime(moment), "Y-m-d H:i")


def _facts(channel: NotificationChannel, form: ChannelForm) -> tuple[Fact, ...]:
    facts = [Fact("Driver", channel.driver)]
    unknown = form.unknown_keys()
    if unknown:
        facts.append(Fact("Settings this driver doesn't declare", ", ".join(unknown)))
    facts += [
        Fact("Created", _when(channel.created_at)),
        Fact("Updated", _when(channel.updated_at)),
    ]
    return tuple(facts)


def _form(data, channel, driver: str, can_change: bool, carried: dict | None) -> ChannelForm:
    if carried is None:
        return ChannelForm(data, channel=channel, driver=driver, disabled=not can_change)
    form = ChannelForm(channel=channel, driver=driver, disabled=not can_change)
    form.initial.update(carried)
    return form


def build_channel_editor(
    pk: int | None,
    data=None,
    *,
    driver: str | None = None,
    can_change: bool,
    digest: str | None = None,
    carried: dict | None = None,
) -> ChannelEditor | None:
    """The editor for channel ``pk``, or for a new channel on ``driver`` when ``pk`` is None.

    ``carried`` opens the form unbound on ``driver``, keeping these posted base fields, so
    a driver switch shows the new driver's defaults. ``digest`` keeps the one a re-rendered
    submit carried, so it can still be refused as stale. Raises ``UnknownDriver``.
    """
    channel = None
    if pk is not None:
        channel = NotificationChannel.objects.filter(pk=pk).first()
        if channel is None:
            return None
    key = driver or (channel.driver if channel else "")
    if key not in DRIVER_REGISTRY and not (channel and key == channel.driver):
        raise UnknownDriver(key)
    form = _form(data, channel, key, can_change, carried)
    query = urlencode({"driver": key})
    channels = reverse("console:channels")
    if channel is None:
        return ChannelEditor(
            pk=None,
            name="",
            form=form,
            digest="",
            url=f"{reverse('console:channel-new')}?{query}",
            back_url=channels,
            lanes=(),
            facts=(),
            can_change=can_change,
        )
    stored = {field: getattr(channel, field) for field in FIELDS}
    return ChannelEditor(
        pk=channel.pk,
        name=channel.name,
        form=form,
        digest=channel_digest(stored) if digest is None else digest,
        url=f"{reverse('console:channel', args=[channel.pk])}?{query}",
        back_url=f"{channels}#channel-{channel.pk}",
        lanes=tuple(_lanes([channel]).get(channel.pk, ())),
        facts=_facts(channel, form),
        can_change=can_change,
    )
