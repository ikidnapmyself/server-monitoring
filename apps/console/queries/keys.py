"""Read models for the API keys page and for one key's editor."""

from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from config.api_keys import KEY_FIELDS, APIKeyForm, endpoints_words, key_digest
from config.describing import Action, Fact
from config.models import APIKey


@dataclass(frozen=True)
class KeyRow:
    pk: int
    name: str
    active: bool
    prefix: str
    endpoints: str
    last_used: datetime | None
    edit: Action

    @property
    def anchor(self) -> str:
        return f"key-{self.pk}"

    @property
    def tone(self) -> str:
        return "ok" if self.active else "muted"


@dataclass(frozen=True)
class KeyList:
    rows: tuple[KeyRow, ...]
    checks_on: bool
    can_add: bool = False

    @property
    def add(self) -> Action:
        return Action("Add a key", "add_key", {})


@dataclass(frozen=True)
class KeyEditor:
    pk: int | None
    name: str
    form: APIKeyForm
    digest: str
    url: str
    back_url: str
    facts: tuple[Fact, ...]
    can_change: bool

    @property
    def delete(self) -> Action | None:
        if self.pk is None:
            return None
        return Action("Delete key", "delete_key", {"pk": self.pk})


@dataclass(frozen=True)
class KeyDeletion:
    pk: int
    name: str
    last_used: datetime | None
    url: str
    back_url: str


def build_key_deletion(pk: int) -> KeyDeletion | None:
    key = APIKey.objects.filter(pk=pk).first()
    if key is None:
        return None
    return KeyDeletion(
        pk=key.pk,
        name=key.name,
        last_used=key.last_used_at,
        url=reverse("console:key-delete", args=[key.pk]),
        back_url=reverse("console:key", args=[key.pk]),
    )


def build_key_list(*, can_add: bool = False) -> KeyList:
    rows = tuple(
        KeyRow(
            pk=key.pk,
            name=key.name,
            active=key.is_active,
            prefix=key.prefix,
            endpoints=endpoints_words(key),
            last_used=key.last_used_at,
            edit=Action(key.name, "view_key", {"pk": key.pk}),
        )
        for key in APIKey.objects.order_by("name", "pk")
    )
    return KeyList(rows=rows, checks_on=settings.API_KEY_AUTH_ENABLED, can_add=can_add)


def _when(moment) -> str:
    return date_format(timezone.localtime(moment), "Y-m-d H:i")


def _facts(key: APIKey) -> tuple[Fact, ...]:
    return (
        Fact("Prefix", f"{key.prefix}…"),
        Fact("Created", _when(key.created_at)),
        Fact("Last used", _when(key.last_used_at) if key.last_used_at else "Never"),
    )


def build_key_editor(
    pk: int | None, data=None, *, can_change: bool, digest: str | None = None
) -> KeyEditor | None:
    """The editor for key ``pk``, or for a new key when ``pk`` is None.

    ``digest`` keeps the one a re-rendered submit carried, so it can still be refused as stale.
    """
    key = None
    if pk is not None:
        key = APIKey.objects.filter(pk=pk).first()
        if key is None:
            return None
    form = APIKeyForm(data, instance=key)
    if not can_change:
        for field in form.fields.values():
            field.disabled = True
    keys = reverse("console:keys")
    if key is None:
        return KeyEditor(
            pk=None,
            name="",
            form=form,
            digest="",
            url=reverse("console:key-new"),
            back_url=keys,
            facts=(),
            can_change=can_change,
        )
    stored = {field: getattr(key, field) for field in KEY_FIELDS}
    return KeyEditor(
        pk=key.pk,
        name=key.name,
        form=form,
        digest=key_digest(stored) if digest is None else digest,
        url=reverse("console:key", args=[key.pk]),
        back_url=f"{keys}#key-{key.pk}",
        facts=_facts(key),
        can_change=can_change,
    )
