"""Saving a channel from the console, and the lanes a delete would cut."""

import hashlib
import json

from django.utils import timezone

from apps.notify.models import NotificationChannel

FIELDS = ("name", "driver", "is_active", "description", "config")


class StaleChannel(Exception):
    """The channel changed between the editor's render and its save."""


def channel_digest(row: dict) -> str:
    """The channel an editor was rendered from, as one value its form carries back."""
    shown = {field: row[field] for field in FIELDS}
    return hashlib.sha256(json.dumps(shown, sort_keys=True, default=str).encode()).hexdigest()


def _stored(pk: int) -> list[dict]:
    return list(NotificationChannel.objects.filter(pk=pk).values(*FIELDS))


def save_channel(pk: int, values: dict, *, expected: str) -> None:
    """Write ``values`` iff the stored row still hashes to ``expected``."""
    stored = _stored(pk)
    if not stored or channel_digest(stored[0]) != expected:
        raise StaleChannel
    # SQLite ignores row locks. Matching the row just read means a save that landed in
    # between wins, and this update matches no row.
    updated = NotificationChannel.objects.filter(pk=pk, **stored[0]).update(
        **values, updated_at=timezone.now()
    )
    if not updated:
        raise StaleChannel


def lanes_cut_by_delete(channel: NotificationChannel) -> list:
    """The lanes on this channel that notify. A delete sets their channel to NULL."""
    # apps.orchestration imports notify's drivers, so a top-level import closes a cycle.
    from apps.orchestration.models import PipelineDefinition

    lanes = PipelineDefinition.objects.filter(channel=channel).order_by("priority", "id")
    return [lane for lane in lanes if isinstance(lane.stages, list) and "notify" in lane.stages]
