"""A form with one typed field per declared setting, writing back a lossless config dict."""

from collections.abc import Mapping
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from config.config_fields import ConfigField

PREFIX = "cfg"
SET_HELP = "Set. Leave blank to keep it."
UNSET_HELP = "Not set."
ALIAS_HELP = "Set under an older name. Leave blank to keep it."
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
    if spec.kind == "float":
        return forms.FloatField(min_value=0, **common)
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


class TypedConfigForm(forms.Form):
    """Secrets are write-only, and a stored value posted back untouched is kept as stored."""

    def __init__(self, data: Any = None, *, stored: Any, specs, aliases: Mapping[str, str]):
        super().__init__(data)
        self._base = dict(stored) if isinstance(stored, dict) else {}
        self._specs: tuple[ConfigField, ...] = tuple(specs)
        self._aliases = dict(aliases)
        self._display: dict[str, Any] = {}
        self._config: dict = {}
        for spec in self._specs:
            self._add_field(spec)

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

    def clean(self) -> dict:
        super().clean()
        cleaned = self.cleaned_data
        untouched = self._untouched()
        for name in untouched:
            self.errors.pop(name, None)
        if self.errors:
            return cleaned
        self._config = self._build_config(untouched)
        for spec in self._specs:
            if spec.required and spec.key not in self._config:
                self.add_error(field_name(spec.key), f"{spec.label} is required.")
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

    def unknown_keys(self) -> list[str]:
        declared = {spec.key for spec in self._specs}
        return sorted(key for key in self._base if key not in declared)
