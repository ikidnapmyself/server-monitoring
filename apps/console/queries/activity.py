"""Read model for the Activity page. Returns dataclasses, never querysets."""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db.models import Model
from django.urls import reverse

from apps.alerts.models import Alert, Incident, Node
from apps.console.describing import KINDS, EntryWords, actor_action, describe_entry
from apps.console.filters import ActivityFilter
from apps.console.models import AuditEntry
from apps.intelligence.models import IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition, PipelineRun
from config.describing import Action
from config.models import APIKey

PAGE_SIZE = 50
MODELS: dict[str, type[Model]] = {
    "route": PipelineDefinition,
    "channel": NotificationChannel,
    "provider": IntelligenceProvider,
    "key": APIKey,
    "user": get_user_model(),
    "incident": Incident,
    "run": PipelineRun,
    "node": Node,
    "alert": Alert,
}


@dataclass(frozen=True)
class ActivityRow:
    pk: int
    at: datetime
    actor: Action
    words: EntryWords


@dataclass(frozen=True)
class ActivityList:
    rows: tuple[ActivityRow, ...]
    total: int
    page: int
    pages: int
    prev_url: str
    next_url: str
    actors: tuple[tuple[int, str], ...]
    actor: int | None
    kinds: tuple[tuple[str, str], ...]
    kind: str
    notice: str
    empty: str
    url: str


def _matching(criteria: ActivityFilter):
    entries = AuditEntry.objects.all()
    if criteria.actor:
        entries = entries.filter(actor_id=criteria.actor)
    if criteria.kind:
        entries = entries.filter(target_kind=criteria.kind)
    return entries


def _existing(entries: list[AuditEntry]) -> set[tuple[str, int]]:
    """The targets on this page whose rows are still there."""
    wanted: dict[str, set[int]] = defaultdict(set)
    for entry in entries:
        if entry.target_kind in MODELS and entry.target_id is not None:
            wanted[entry.target_kind].add(entry.target_id)
    found = set()
    for kind, pks in wanted.items():
        rows = MODELS[kind]._default_manager.filter(pk__in=pks).values_list("pk", flat=True)
        found |= {(kind, pk) for pk in rows}
    return found


def _actors() -> tuple[tuple[int, str], ...]:
    users = get_user_model().objects.filter(
        pk__in=AuditEntry.objects.exclude(actor=None).values("actor_id")
    )
    return tuple((user.pk, user.get_username()) for user in users.order_by("username", "pk"))


def build_activity_list(criteria: ActivityFilter) -> ActivityList:
    base = reverse("console:activity")
    entries = _matching(criteria)
    total = entries.count()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(criteria.page, pages)
    criteria = replace(criteria, page=page)

    if total:
        start = (page - 1) * PAGE_SIZE
        found = list(entries[start : start + PAGE_SIZE])
        existing = _existing(found)
        rows = tuple(
            ActivityRow(
                pk=entry.pk,
                at=entry.at,
                actor=actor_action(entry),
                words=describe_entry(
                    entry, exists=(entry.target_kind, entry.target_id) in existing
                ),
            )
            for entry in found
        )
        empty = ""
    elif not AuditEntry.objects.exists():
        rows, empty = (), "none"
    else:
        rows, empty = (), "no_match"

    return ActivityList(
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        prev_url=base + criteria.querystring(page=page - 1) if page > 1 else "",
        next_url=base + criteria.querystring(page=page + 1) if page < pages else "",
        actors=_actors(),
        actor=criteria.actor,
        kinds=tuple((key, word[0].upper() + word[1:]) for key, word in KINDS.items()),
        kind=criteria.kind,
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
