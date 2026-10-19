"""Saving a row from an editor only when nobody changed it since the editor opened."""

import json

from django.utils import timezone
from django.utils.crypto import salted_hmac


class Stale(Exception):
    """The row changed between the editor's render and its save."""


def row_digest(salt: str, row: dict, fields: tuple[str, ...]) -> str:
    """The row an editor was rendered from, as one value its form carries back.

    Keyed on the secret key: a row can hold credentials, and a plain hash on the page
    would let anyone try passwords against it.
    """
    shown = {field: row[field] for field in fields}
    payload = json.dumps(shown, sort_keys=True, default=str)
    return salted_hmac(salt, payload, algorithm="sha256").hexdigest()


def _stored(model, pk: int, fields: tuple[str, ...]) -> list[dict]:
    return list(model.objects.filter(pk=pk).values(*fields))


def save_if_unchanged(
    model, pk: int, fields: tuple[str, ...], values: dict, *, salt: str, expected: str
) -> None:
    """Write ``values`` iff the stored row still hashes to ``expected``."""
    stored = _stored(model, pk, fields)
    if not stored or row_digest(salt, stored[0], fields) != expected:
        raise Stale
    # SQLite ignores row locks. Matching the row just read means a save that landed in
    # between wins, and this update matches no row.
    updated = model.objects.filter(pk=pk, **stored[0]).update(**values, updated_at=timezone.now())
    if not updated:
        raise Stale
