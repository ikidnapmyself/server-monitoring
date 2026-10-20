"""Saving a channel from the console, and the lanes a delete would cut."""

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from config.stale import Stale, row_digest, save_if_unchanged

FIELDS = ("name", "driver", "is_active", "description", "config")
SALT = "notify.channel"


class StaleChannel(Stale):
    """The channel changed between the editor's render and its save."""


def channel_digest(row: dict) -> str:
    return row_digest(SALT, row, FIELDS)


def save_channel(pk: int, values: dict, *, expected: str) -> None:
    try:
        save_if_unchanged(NotificationChannel, pk, FIELDS, values, salt=SALT, expected=expected)
    except Stale as exc:
        raise StaleChannel from exc


def lanes_cut_by_delete(channel: NotificationChannel) -> list[PipelineDefinition]:
    """The lanes on this channel that notify. A delete sets their channel to NULL."""
    lanes = PipelineDefinition.objects.filter(channel=channel).order_by("priority", "id")
    return [lane for lane in lanes if isinstance(lane.stages, list) and "notify" in lane.stages]
