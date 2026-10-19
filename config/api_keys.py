"""The console's API key form, and the save that refuses a key changed since it was shown."""

from django import forms
from django.core.exceptions import ValidationError

from config.config_form import ListField
from config.describing import format_list
from config.models import APIKey
from config.stale import Stale, row_digest, save_if_unchanged

KEY_FIELDS = ("name", "is_active", "allowed_endpoints")
SALT = "config.apikey"
PATH_ERROR = "Each line must be a path starting with /."


class APIKeyForm(forms.ModelForm):
    allowed_endpoints = ListField(
        required=False,
        label="Allowed endpoints",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=(
            "One path per line, such as /alerts/webhook/. Leave empty to allow every endpoint."
        ),
    )

    class Meta:
        model = APIKey
        fields = KEY_FIELDS
        labels = {"name": "Name", "is_active": "On"}
        help_texts = {"name": "", "is_active": "A key that's off is refused."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["allowed_endpoints"] = "\n".join(self.instance.allowed_endpoints)

    def clean_allowed_endpoints(self) -> list[str]:
        paths = self.cleaned_data["allowed_endpoints"]
        if any(not path.startswith("/") for path in paths):
            raise ValidationError(PATH_ERROR)
        return paths


class StaleKey(Stale):
    """The key changed between the editor's render and its save."""


def key_digest(row: dict) -> str:
    return row_digest(SALT, row, KEY_FIELDS)


def save_key(pk: int, values: dict, *, expected: str) -> None:
    edited = {field: values[field] for field in KEY_FIELDS if field in values}
    try:
        save_if_unchanged(APIKey, pk, KEY_FIELDS, edited, salt=SALT, expected=expected)
    except Stale as exc:
        raise StaleKey from exc


def endpoints_words(key: APIKey) -> str:
    return format_list(key.allowed_endpoints) or "All endpoints"
