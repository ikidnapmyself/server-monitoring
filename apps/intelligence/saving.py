"""Saving a provider from the console, and which provider the pipeline uses now."""

from django.db import transaction
from django.utils import timezone

from apps.intelligence.models import IntelligenceProvider
from config.stale import Stale, row_digest, save_if_unchanged

FIELDS = ("name", "provider", "is_active", "description", "config")
SALT = "intelligence.provider"


class StaleProvider(Stale):
    """The provider changed between the editor's render and its save."""


def provider_digest(row: dict) -> str:
    return row_digest(SALT, row, FIELDS)


def save_provider(pk: int, values: dict, *, expected: str) -> None:
    """A conditional update skips ``IntelligenceProvider.save()``, so it turns the others off."""
    try:
        with transaction.atomic():
            if values.get("is_active"):
                IntelligenceProvider.objects.filter(is_active=True).exclude(pk=pk).update(
                    is_active=False, updated_at=timezone.now()
                )
            save_if_unchanged(
                IntelligenceProvider, pk, FIELDS, values, salt=SALT, expected=expected
            )
    except Stale as exc:
        raise StaleProvider from exc


def active_provider() -> IntelligenceProvider | None:
    return IntelligenceProvider.objects.filter(is_active=True).first()
