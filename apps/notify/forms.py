"""The console's channel form: one typed field per setting a driver declares."""

from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError

from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.models import NotificationChannel
from config.config_form import TypedConfigForm, clear_name, field_name

UNKNOWN_DRIVER = "A driver this hub doesn't have"
CHANNEL_FIELDS = ("name", "driver", "is_active", "description", "config")
NAME_TAKEN = "Another channel already has this name."


class ChannelForm(TypedConfigForm):
    name = forms.CharField(max_length=100)
    driver = forms.ChoiceField()
    is_active = forms.BooleanField(required=False, label="Active")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(
        self,
        data: Any = None,
        *,
        channel: NotificationChannel | None = None,
        driver: str | None = None,
    ):
        self.channel = channel
        self.driver_key = driver or (channel.driver if channel else "")
        stored = channel.config if channel else {}
        base = dict(stored) if isinstance(stored, dict) else {}
        if channel and self.driver_key != channel.driver:
            old_cls = DRIVER_REGISTRY.get(channel.driver)
            if old_cls is None:
                base = {}
            else:
                for key in self._credentials(old_cls):
                    base.pop(key, None)
        driver_cls = DRIVER_REGISTRY.get(self.driver_key)
        self.driver_label = driver_cls.label if driver_cls else UNKNOWN_DRIVER
        super().__init__(
            data,
            stored=base,
            specs=driver_cls.all_config_fields() if driver_cls else (),
            aliases=driver_cls.credential_aliases if driver_cls else {},
        )

        choices = [(key, cls.label) for key, cls in DRIVER_REGISTRY.items()]
        if channel and channel.driver not in DRIVER_REGISTRY:
            choices.append((channel.driver, UNKNOWN_DRIVER))
        cast(forms.ChoiceField, self.fields["driver"]).choices = choices
        self.initial.update(
            name=channel.name if channel else "",
            driver=self.driver_key,
            is_active=channel.is_active if channel else True,
            description=channel.description if channel else "",
        )

    @staticmethod
    def _credentials(driver_cls) -> list[str]:
        secrets = [spec.key for spec in driver_cls.all_config_fields() if spec.secret]
        return secrets + list(driver_cls.credential_aliases)

    def clean_name(self) -> str:
        name = self.cleaned_data["name"]
        others = NotificationChannel.objects.filter(name=name)
        if self.channel:
            others = others.exclude(pk=self.channel.pk)
        if others.exists():
            raise ValidationError(NAME_TAKEN)
        return name

    def clean(self) -> dict:
        if self.cleaned_data.get("driver", self.driver_key) != self.driver_key:
            self.add_error("driver", "Show this driver's settings before saving.")
        cleaned = super().clean()
        driver_cls = DRIVER_REGISTRY.get(self.driver_key)
        if driver_cls and not self.errors and not driver_cls().validate_config(self._config):
            raise ValidationError(
                f"{driver_cls.label} can't send with these settings. Check the required fields."
            )
        return cleaned

    def channel_fields(self) -> dict:
        fields = {name: self.cleaned_data[name] for name in CHANNEL_FIELDS[:-1]}
        fields["config"] = self.config()
        return fields

    def groups(self) -> list[tuple[str, list[forms.BoundField]]]:
        settings: list[forms.BoundField] = []
        templates: list[forms.BoundField] = []
        for spec in self._specs:
            bound = self[field_name(spec.key)]
            if spec.kind == "template":
                templates.append(bound)
                continue
            settings.append(bound)
            if clear_name(spec.key) in self.fields:
                settings.append(self[clear_name(spec.key)])
        return [("Settings", settings), ("Message templates", templates)]
