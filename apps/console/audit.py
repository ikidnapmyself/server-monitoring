"""Records console writes as ``AuditEntry`` rows. Call ``record`` inside the write's transaction."""

import datetime
import json
from collections.abc import Iterable
from typing import Any

from django.db import models

from apps.console.models import AuditEntry
from config.client_ip import client_ip

Target = models.Model | tuple[str, int | None, str] | None

NAME_LENGTH = AuditEntry._meta.get_field("target_name").max_length or 255


def record(request, action: str, target: Target = None, *, changes=None, actor=None) -> AuditEntry:
    if actor is None:
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
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def signed_in(sender, request, user, **kwargs):
    record(request, "session.sign_in", user, actor=user)


def signed_out(sender, request, user, **kwargs):
    if user is not None:
        record(request, "session.sign_out", user)


def sign_in_failed(sender, credentials, request=None, **kwargs):
    if request is not None:
        record(request, "session.sign_in_failed", ("user", None, credentials.get("username") or ""))
