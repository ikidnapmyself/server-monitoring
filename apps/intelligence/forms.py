"""The console's provider form: one typed field per setting a provider declares."""

from typing import Any, cast

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.providers import PROVIDERS
from apps.intelligence.providers.base import SENSITIVE_PATTERNS
from config.config_form import TypedConfigForm, clear_name, field_name
from config.security.url_validation import URLNotAllowedError, validate_safe_url

UNKNOWN_PROVIDER = "A provider this hub doesn't have"
PROVIDER_FIELDS = ("name", "provider", "is_active", "description", "config")
NAME_TAKEN = "Another provider already has this name."
UNSAFE_URL = (
    "This hub won't call that address. Its host must be public, or allowed in the hub's settings."
)
LABELS = dict(IntelligenceProvider.PROVIDER_CHOICES)


def provider_class(kind: str):
    return PROVIDERS.get(kind) if kind in LABELS else None


def _carries_over(key: str, old_cls) -> bool:
    secrets = {spec.key for spec in old_cls.all_config_fields() if spec.secret}
    if key in secrets or key in old_cls.unused_config:
        return False
    return not SENSITIVE_PATTERNS & set(key.lower().split("_"))


class ProviderForm(TypedConfigForm):
    name = forms.CharField(max_length=100)
    provider = forms.ChoiceField()
    is_active = forms.BooleanField(required=False, label="Active")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(
        self,
        data: Any = None,
        *,
        provider: IntelligenceProvider | None = None,
        kind: str | None = None,
    ):
        self.instance = provider
        self.kind = kind or (provider.provider if provider else "")
        stored = provider.config if provider else {}
        base = dict(stored) if isinstance(stored, dict) else {}
        if provider and self.kind != provider.provider:
            old_cls = provider_class(provider.provider)
            base = {k: v for k, v in base.items() if old_cls and _carries_over(k, old_cls)}
        cls = provider_class(self.kind)
        self.provider_label = LABELS.get(self.kind, UNKNOWN_PROVIDER)
        super().__init__(
            data, stored=base, specs=cls.all_config_fields() if cls else (), aliases={}
        )

        choices = list(IntelligenceProvider.PROVIDER_CHOICES)
        if provider and provider.provider not in LABELS:
            choices.append((provider.provider, UNKNOWN_PROVIDER))
        cast(forms.ChoiceField, self.fields["provider"]).choices = choices
        self.initial.update(
            name=provider.name if provider else "",
            provider=self.kind,
            is_active=provider.is_active if provider else False,
            description=provider.description if provider else "",
        )

    def clean_name(self) -> str:
        name = self.cleaned_data["name"]
        others = IntelligenceProvider.objects.filter(name=name)
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise ValidationError(NAME_TAKEN)
        return name

    def clean(self) -> dict:
        if self.cleaned_data.get("provider", self.kind) != self.kind:
            self.add_error("provider", "Show this provider's settings before saving.")
        cleaned = super().clean()
        for spec in self._specs:
            name = field_name(spec.key)
            value = cleaned.get(name) or spec.default
            if spec.kind != "url" or not value or name in self.errors:
                continue
            try:
                validate_safe_url(value, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)
            except URLNotAllowedError:
                self.add_error(name, UNSAFE_URL)
        return cleaned

    def provider_fields(self) -> dict:
        fields = {name: self.cleaned_data[name] for name in PROVIDER_FIELDS[:-1]}
        fields["config"] = self.config()
        return fields

    def groups(self) -> list[tuple[str, list[forms.BoundField]]]:
        settings_group: list[forms.BoundField] = []
        for spec in self._specs:
            settings_group.append(self[field_name(spec.key)])
            if clear_name(spec.key) in self.fields:
                settings_group.append(self[clear_name(spec.key)])
        return [("Settings", settings_group)]
