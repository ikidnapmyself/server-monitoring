"""Records console writes as ``AuditEntry`` rows. Call ``record`` inside the write's transaction."""

import json
from collections.abc import Iterable
from typing import Any

from django.db import models

from apps.console.models import AuditEntry
from config.client_ip import client_ip

Target = models.Model | tuple[str, int | None, str] | None

NAME_LENGTH = AuditEntry._meta.get_field("target_name").max_length or 255


def record(request, action: str, target: Target = None, *, changes=None) -> AuditEntry:
    user = getattr(request, "user", None)
    actor = user if user is not None and user.is_authenticated else None
    if isinstance(target, models.Model):
        kind, pk, name = target._meta.model_name or "", target.pk, str(target)
    elif target is None:
        kind, pk, name = "", None, ""
    else:
        kind, pk, name = target
    return AuditEntry.objects.create(
        actor=actor,
        actor_name=actor.get_username() if actor else "",
        action=action,
        target_kind=kind,
        target_id=pk,
        target_name=name[:NAME_LENGTH],
        changes=changes or {},
        address=client_ip(request),
    )


def diff(
    before: dict[str, Any], after: dict[str, Any], *, secret: frozenset[str] = frozenset()
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for field in sorted(before.keys() | after.keys()):
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        changes[field] = {"changed": True} if field in secret else {"old": old, "new": new}
    return changes


def snapshot(instance, fields: Iterable[str]) -> dict[str, Any]:
    return {field: _json_safe(getattr(instance, field)) for field in fields}


def _json_safe(value):
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value
