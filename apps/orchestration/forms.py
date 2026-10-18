"""Operator-facing forms for ``apps.orchestration``: a route and its condition rows."""

from typing import Any

from django import forms
from django.http import QueryDict

from apps.notify.models import NotificationChannel
from apps.orchestration.describing import STAGE_NAMES
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.routing import (
    CHOICES,
    FIELDS,
    MEMBERSHIP,
    OPS,
    representable,
    split_field,
    value_words,
)

PREFIX = "cond"
ROUTABLE_STAGES = PipelineDefinition.ROUTABLE_STAGES
MODEL_FIELDS = ["name", "description", "is_active", "priority", "channel"]


class ConditionForm(forms.Form):
    field = forms.ChoiceField(choices=FIELDS.items())
    label_key = forms.CharField(required=False, label="Label name")
    op = forms.ChoiceField(choices=OPS.items(), label="Rule")
    value = forms.CharField(
        required=False, help_text="For “is one of”, separate values with commas."
    )

    @staticmethod
    def initial_for(cond: dict) -> dict:
        kind, key = split_field(cond["field"])
        value = cond["value"]
        values = value if cond["op"] in MEMBERSHIP else [value]
        said = ", ".join(value_words(kind, v) for v in values)
        return {"field": kind, "label_key": key, "op": cond["op"], "value": said}

    def clean(self) -> dict:
        super().clean()
        cleaned = self.cleaned_data
        kind, op, raw = cleaned.get("field"), cleaned.get("op"), cleaned.get("value")
        self.condition: dict | None = None
        if not raw and not cleaned.get("label_key"):
            return cleaned
        if not raw:
            self.add_error("value", self.fields["value"].error_messages["required"])
        if kind == "label" and not cleaned.get("label_key"):
            self.add_error("label_key", "Name the label to match on.")
        if kind is None or op is None or not raw:
            return cleaned
        if op in MEMBERSHIP:
            values = [v.strip() for v in raw.split(",") if v.strip()]
            if not values:
                self.add_error("value", "Give at least one value.")
                return cleaned
        else:
            values = [raw]
        if kind in CHOICES:
            values = self._choice_values(kind, values)
        if self.errors:
            return cleaned
        field = f"label:{cleaned['label_key']}" if kind == "label" else kind
        value: str | list[str] = values if op in MEMBERSHIP else values[0]
        self.condition = {"field": field, "op": op, "value": value}
        return cleaned

    def _choice_values(self, kind: str, values: list[str]) -> list[str]:
        choices = CHOICES[kind]
        known = {str(c.value).lower(): c.value for c in choices}
        known.update({str(c.label).lower(): c.value for c in choices})
        for value in values:
            if value.lower() not in known:
                labels = ", ".join(str(label) for label in choices.labels)
                self.add_error("value", f"{FIELDS[kind]} can't be {value}. Use one of {labels}.")
                return values
        return [known[v.lower()] for v in values]


ConditionFormSet = forms.formset_factory(ConditionForm, extra=0, can_delete=True)


class ChannelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: NotificationChannel) -> str:
        return obj.name if obj.is_active else f"{obj.name} (off)"


class RouteForm(forms.ModelForm):
    stages = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple,
        required=False,
        choices=[(stage, STAGE_NAMES[stage]) for stage in ROUTABLE_STAGES],
    )
    channel = ChannelChoiceField(
        queryset=NotificationChannel.objects.order_by("name"), required=False, label="Channel"
    )

    class Meta:
        model = PipelineDefinition
        fields = MODEL_FIELDS

    def __init__(
        self,
        data: Any = None,
        *,
        instance: PipelineDefinition | None = None,
        disabled: bool = False,
    ):
        super().__init__(data, instance=instance)
        self._stored_match = self.instance.match
        stored = self.instance.pk is not None
        self.fields["stages"].initial = (
            self.instance.routable_stages() if stored else list(ROUTABLE_STAGES)
        )
        rows = self._stored_match if representable(self._stored_match) else None
        if rows is None:
            self.fields["replace_match"] = forms.BooleanField(required=False, label="Replace them")
        initial = [ConditionForm.initial_for(c) for c in rows or []]
        # A row is never empty_permitted: a submitted row that equals its initial would
        # otherwise skip clean() and vanish from the match.
        self.conditions = ConditionFormSet(
            data, initial=initial, prefix=PREFIX, form_kwargs={"empty_permitted": False}
        )
        if disabled:
            for field in self.fields.values():
                field.disabled = True
            for row in self.conditions.forms:
                for field in row.fields.values():
                    field.disabled = True

    @staticmethod
    def add_condition(data: QueryDict) -> QueryDict:
        bumped = data.copy()
        key = f"{PREFIX}-TOTAL_FORMS"
        count = data.get(key, "")
        bumped[key] = str((int(count) if count.isdigit() else 0) + 1)
        return bumped

    def clean_stages(self) -> list[str]:
        chosen = self.cleaned_data["stages"]
        stages = [s for s in ROUTABLE_STAGES if s in chosen]
        # The model's clean() checks instance.stages, so junk stored there would fail this save.
        self.instance.stages = stages
        return stages

    def is_valid(self) -> bool:
        form_ok = super().is_valid()
        rows_ok = self.conditions.is_valid()
        return form_ok and rows_ok

    def route_fields(self) -> dict:
        fields = {name: self.cleaned_data[name] for name in MODEL_FIELDS}
        fields["stages"] = self.cleaned_data["stages"]
        if "replace_match" in self.fields and not self.cleaned_data["replace_match"]:
            fields["match"] = self._stored_match
        else:
            deleted = self.conditions.deleted_forms
            fields["match"] = [
                row.condition
                for row in self.conditions.forms
                if row not in deleted and row.condition is not None
            ]
        return fields
