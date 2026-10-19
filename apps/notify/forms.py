"""The console's channel form: one typed field per setting a driver declares."""

from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.notify.config_fields import ConfigField
from apps.notify.drivers import DRIVER_REGISTRY
from apps.notify.models import NotificationChannel

PREFIX = "cfg"
UNKNOWN_DRIVER = "A driver this hub doesn't have"
CHANNEL_FIELDS = ("name", "driver", "is_active", "description", "config")
SET_HELP = "Set. Leave blank to keep it."
UNSET_HELP = "Not set."
ALIAS_HELP = "Set under an older name. Leave blank to keep it."
NAME_TAKEN = "Another channel already has this name."
HEADER_ERROR = "Write each header on its own line as Name: value."


def field_name(key: str) -> str:
    return f"{PREFIX}__{key}"


def clear_name(key: str) -> str:
    return f"{field_name(key)}__clear"


def _unix(value: Any) -> Any:
    return value.replace("\r\n", "\n") if isinstance(value, str) else value


def _blank(value: Any) -> bool:
    return value in (None, "", [], {})


class SecretTextarea(forms.Textarea):
    def format_value(self, value: Any) -> None:
        return None


class TemplateField(forms.CharField):
    def to_python(self, value: Any) -> str:
        return _unix(super().to_python(value))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


class ListField(forms.Field):
    def to_python(self, value: Any) -> list[str]:
        text = _text(value)
        return [line.strip() for line in text.splitlines() if line.strip()]


class HeadersField(forms.Field):
    def to_python(self, value: Any) -> dict[str, str]:
        headers = {}
        for line in _text(value).splitlines():
            if not line.strip():
                continue
            name, colon, header = line.partition(":")
            if not colon or not name.strip():
                raise ValidationError(HEADER_ERROR)
            headers[name.strip()] = header.strip()
        return headers


def _display(spec: ConfigField, value: Any) -> Any:
    """A stored value as its field shows it, so an untouched field posts it back unchanged."""
    if spec.kind == "bool":
        return bool(value)
    if spec.kind == "list" and isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def _typed_field(spec: ConfigField) -> forms.Field:
    common: dict[str, Any] = {"label": spec.label, "required": False, "help_text": spec.help}
    if spec.kind == "url":
        return forms.CharField(validators=[URLValidator(schemes=["http", "https"])], **common)
    if spec.kind == "int":
        return forms.IntegerField(min_value=1, **common)
    if spec.kind == "bool":
        return forms.BooleanField(**common)
    if spec.kind == "choice":
        return forms.ChoiceField(choices=list(spec.choices), **common)
    if spec.kind == "list":
        return ListField(widget=forms.Textarea, **common)
    if spec.kind == "template":
        return TemplateField(widget=forms.Textarea, strip=False, **common)
    if spec.kind == "headers":
        return HeadersField(widget=SecretTextarea, **common)
    return forms.CharField(**common)


class ChannelForm(forms.Form):
    name = forms.CharField(max_length=100)
    driver = forms.ChoiceField()
    is_active = forms.BooleanField(required=False, label="Active")
    description = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(
        self,
        data: Any = None,
        *,
        channel: NotificationChannel | None = None,
        driver: str | None = None,
        disabled: bool = False,
    ):
        super().__init__(data)
        self.channel = channel
        self.driver_key = driver or (channel.driver if channel else "")
        stored = channel.config if channel else {}
        self._base = dict(stored) if isinstance(stored, dict) else {}
        if channel and self.driver_key != channel.driver:
            old_cls = DRIVER_REGISTRY.get(channel.driver)
            if old_cls is None:
                self._base = {}
            else:
                for key in self._credentials(old_cls):
                    self._base.pop(key, None)
        driver_cls = DRIVER_REGISTRY.get(self.driver_key)
        self.driver_label = driver_cls.label if driver_cls else UNKNOWN_DRIVER
        self._specs = driver_cls.all_config_fields() if driver_cls else ()
        self._display: dict[str, Any] = {}
        self._config: dict = {}
        self._aliases = dict(driver_cls.credential_aliases) if driver_cls else {}

        choices = [(key, cls.label) for key, cls in DRIVER_REGISTRY.items()]
        if channel and channel.driver not in DRIVER_REGISTRY:
            choices.append((channel.driver, UNKNOWN_DRIVER))
        cast(forms.ChoiceField, self.fields["driver"]).choices = choices
        self.initial = {
            "name": channel.name if channel else "",
            "driver": self.driver_key,
            "is_active": channel.is_active if channel else True,
            "description": channel.description if channel else "",
        }
        for spec in self._specs:
            self._add_field(spec)
        if disabled:
            for field in self.fields.values():
                field.disabled = True

    @staticmethod
    def _credentials(driver_cls) -> list[str]:
        secrets = [spec.key for spec in driver_cls.all_config_fields() if spec.secret]
        return secrets + list(driver_cls.credential_aliases)

    def _add_field(self, spec: ConfigField) -> None:
        name = field_name(spec.key)
        field = _typed_field(spec)
        self.fields[name] = field
        is_stored = spec.key in self._base
        if spec.secret:
            if spec.kind != "headers":
                field.widget = forms.PasswordInput(render_value=False)
            by_alias = not is_stored and bool(self._stored_aliases(spec.key))
            state = SET_HELP if is_stored else ALIAS_HELP if by_alias else UNSET_HELP
            field.help_text = f"{spec.help} {state}" if spec.help else state
            if is_stored or by_alias:
                self.fields[clear_name(spec.key)] = forms.BooleanField(
                    required=False, label="Clear"
                )
            return
        shown = _display(spec, self._base[spec.key] if is_stored else spec.default)
        if is_stored:
            self._display[name] = shown
        if isinstance(field, forms.ChoiceField):
            choices = list(spec.choices)
            if is_stored and shown not in {value for value, _ in choices}:
                choices.append((shown, f"{shown} (stored)"))
            if spec.default is None:
                choices.insert(0, ("", "Not set"))
            field.choices = choices
        self.initial[name] = shown

    def _stored_aliases(self, key: str) -> list[str]:
        return [
            alias
            for alias, target in self._aliases.items()
            if target == key and alias in self._base
        ]

    def _untouched(self) -> set[str]:
        """Stored fields posted back as they were shown, which keep their stored value."""
        return {
            name for name, shown in self._display.items() if _unix(self[name].data) == _unix(shown)
        }

    def clean_name(self) -> str:
        name = self.cleaned_data["name"]
        others = NotificationChannel.objects.filter(name=name)
        if self.channel:
            others = others.exclude(pk=self.channel.pk)
        if others.exists():
            raise ValidationError(NAME_TAKEN)
        return name

    def clean(self) -> dict:
        super().clean()
        cleaned = self.cleaned_data
        untouched = self._untouched()
        for name in untouched:
            self.errors.pop(name, None)
        if cleaned.get("driver", self.driver_key) != self.driver_key:
            self.add_error("driver", "Show this driver's settings before saving.")
        if self.errors:
            return cleaned
        self._config = self._build_config(untouched)
        for spec in self._specs:
            if spec.required and spec.key not in self._config:
                self.add_error(field_name(spec.key), f"{spec.label} is required.")
        driver_cls = DRIVER_REGISTRY.get(self.driver_key)
        if driver_cls and not self.errors and not driver_cls().validate_config(self._config):
            raise ValidationError(
                f"{driver_cls.label} can't send with these settings. Check the required fields."
            )
        return cleaned

    def _build_config(self, untouched: set[str]) -> dict:
        config = dict(self._base)
        for spec in self._specs:
            name = field_name(spec.key)
            if name in untouched:
                continue
            value = self.cleaned_data[name]
            if spec.secret:
                if not _blank(value):
                    config[spec.key] = value
                elif self.cleaned_data.get(clear_name(spec.key)):
                    for key in [spec.key, *self._stored_aliases(spec.key)]:
                        config.pop(key, None)
            elif spec.kind != "bool" and _blank(value):
                config.pop(spec.key, None)
            elif spec.key in self._base or value != spec.default:
                config[spec.key] = value
        return config

    def config(self) -> dict:
        return self._config

    def channel_fields(self) -> dict:
        fields = {name: self.cleaned_data[name] for name in CHANNEL_FIELDS[:-1]}
        fields["config"] = self.config()
        return fields

    def unknown_keys(self) -> list[str]:
        declared = {spec.key for spec in self._specs}
        return sorted(key for key in self._base if key not in declared)

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
