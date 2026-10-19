"""Read models for the providers page and for one provider's editor."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from apps.intelligence.forms import LABELS, UNKNOWN_PROVIDER, ProviderForm
from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.saving import FIELDS, active_provider, provider_digest
from config.describing import Action, Fact

FALLBACK = "No provider is on, so analysis uses the built-in local analyzer."
FALLS_BACK = "Analysis falls back to the built-in local analyzer."


class UnknownProvider(Exception):
    """A provider key that is neither one this hub offers nor the provider's own."""


def _label(kind: str) -> str:
    return LABELS.get(kind, UNKNOWN_PROVIDER)


def analysis_uses(active: IntelligenceProvider | None) -> str:
    return f"Analysis uses {active.name}." if active else FALLBACK


@dataclass(frozen=True)
class ProviderRow:
    pk: int
    name: str
    kind_label: str
    active: bool
    description: str
    edit: Action

    @property
    def anchor(self) -> str:
        return f"provider-{self.pk}"

    @property
    def tone(self) -> str:
        return "ok" if self.active else "muted"


@dataclass(frozen=True)
class ProviderList:
    rows: tuple[ProviderRow, ...]
    headline: str
    can_add: bool = False

    @property
    def add(self) -> Action:
        return Action("Add a provider", "add_provider", {})


@dataclass(frozen=True)
class KindChoice:
    key: str
    label: str
    url: str


@dataclass(frozen=True)
class ProviderEditor:
    pk: int | None
    name: str
    form: ProviderForm
    digest: str
    url: str
    back_url: str
    facts: tuple[Fact, ...]
    can_change: bool

    @property
    def delete(self) -> Action | None:
        if self.pk is None:
            return None
        return Action("Delete provider", "delete_provider", {"pk": self.pk})


@dataclass(frozen=True)
class ProviderDeletion:
    pk: int
    name: str
    consequence: str
    url: str
    back_url: str


def build_provider_deletion(pk: int) -> ProviderDeletion | None:
    provider = IntelligenceProvider.objects.filter(pk=pk).first()
    if provider is None:
        return None
    active = active_provider()
    if provider.is_active or active is None:
        consequence = FALLS_BACK
    else:
        consequence = f"Analysis keeps using {active.name}."
    return ProviderDeletion(
        pk=provider.pk,
        name=provider.name,
        consequence=consequence,
        url=reverse("console:provider-delete", args=[provider.pk]),
        back_url=reverse("console:provider", args=[provider.pk]),
    )


def build_provider_list(*, can_add: bool = False) -> ProviderList:
    providers = list(IntelligenceProvider.objects.order_by("-is_active", "name"))
    rows = tuple(
        ProviderRow(
            pk=provider.pk,
            name=provider.name,
            kind_label=_label(provider.provider),
            active=provider.is_active,
            description=provider.description,
            edit=Action(provider.name, "view_provider", {"pk": provider.pk}),
        )
        for provider in providers
    )
    active = next((provider for provider in providers if provider.is_active), None)
    return ProviderList(rows=rows, headline=analysis_uses(active), can_add=can_add)


def new_provider_url(kind: str) -> str:
    return f"{reverse('console:provider-new')}?{urlencode({'provider': kind})}"


def provider_choices() -> tuple[KindChoice, ...]:
    return tuple(
        KindChoice(key, label, new_provider_url(key))
        for key, label in IntelligenceProvider.PROVIDER_CHOICES
    )


def _when(moment) -> str:
    return date_format(timezone.localtime(moment), "Y-m-d H:i")


def _facts(provider: IntelligenceProvider, form: ProviderForm) -> tuple[Fact, ...]:
    facts = [Fact("Provider", provider.provider)]
    unknown = form.unknown_keys()
    if unknown:
        facts.append(Fact("Settings this provider doesn't declare", ", ".join(unknown)))
    facts += [
        Fact("Created", _when(provider.created_at)),
        Fact("Updated", _when(provider.updated_at)),
    ]
    return tuple(facts)


def _form(data, provider, kind: str, can_change: bool, carried: dict | None) -> ProviderForm:
    if carried is None:
        form = ProviderForm(data, provider=provider, kind=kind, disabled=not can_change)
    else:
        form = ProviderForm(provider=provider, kind=kind, disabled=not can_change)
        form.initial.update(carried)
    active = active_provider()
    if active and (provider is None or active.pk != provider.pk):
        form.fields["is_active"].help_text = f"Turning this on turns off {active.name}."
    return form


def build_provider_editor(
    pk: int | None,
    data=None,
    *,
    kind: str | None = None,
    can_change: bool,
    digest: str | None = None,
    carried: dict | None = None,
) -> ProviderEditor | None:
    """The editor for provider ``pk``, or for a new provider of ``kind`` when ``pk`` is None.

    ``carried`` opens the form unbound on ``kind``, keeping these posted base fields, so a
    switch shows the new kind's defaults. ``digest`` keeps the one a re-rendered submit
    carried, so it can still be refused as stale. Raises ``UnknownProvider``.
    """
    provider = None
    if pk is not None:
        provider = IntelligenceProvider.objects.filter(pk=pk).first()
        if provider is None:
            return None
    key = kind or (provider.provider if provider else "")
    if key not in LABELS and not (provider and key == provider.provider):
        raise UnknownProvider(key)
    form = _form(data, provider, key, can_change, carried)
    query = urlencode({"provider": key})
    providers = reverse("console:providers")
    if provider is None:
        return ProviderEditor(
            pk=None,
            name="",
            form=form,
            digest="",
            url=f"{reverse('console:provider-new')}?{query}",
            back_url=providers,
            facts=(),
            can_change=can_change,
        )
    stored = {field: getattr(provider, field) for field in FIELDS}
    return ProviderEditor(
        pk=provider.pk,
        name=provider.name,
        form=form,
        digest=provider_digest(stored) if digest is None else digest,
        url=f"{reverse('console:provider', args=[provider.pk])}?{query}",
        back_url=f"{providers}#provider-{provider.pk}",
        facts=_facts(provider, form),
        can_change=can_change,
    )
