"""Operator-facing forms for ``apps.alerts``.

``NodePolicyForm`` is the editor half of the bargain described in
``apps.alerts.node_policy``: the scorers in ``apps.alerts.reevaluation`` run in
the ingest path and are fail-open, so a threshold they cannot use is silently
indistinguishable from no threshold at all. Here a human is typing, so the same
rules are enforced as field errors instead.
"""

from django import forms

from apps.alerts.models import Node
from apps.alerts.node_policy import (
    PolicyError,
    clean_int_list,
    clean_thresholds,
    field_name,
    sections_for,
    spec_for,
    to_config,
    to_form_values,
)


class NodePolicyForm(forms.ModelForm):
    """Edit a node's per-checker policy as typed boxes, never as raw JSON.

    The fields are built in ``__init__`` from ``sections_for(instance)``, so a
    node shows exactly the checkers it reports or already configures, and adding
    a scorer adds a section with no edit here.
    """

    class Meta:
        model = Node
        # The registry fields are written by the ingest path and are read-only in
        # the admin; this form owns nothing but the policy boxes. ``exclude``
        # says the same thing about ``config`` a second time, because the admin
        # regenerates the form with its own field list and the raw JSON must
        # never come back through that door.
        fields: list[str] = []
        exclude = ["config"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sections = self._sections_for_instance()
        for checker in self._sections:
            for field in spec_for(checker):
                self.fields[field_name(checker, field.name)] = self._build_field(field)
        self.initial.update(to_form_values(self.instance.config))
        # What ``save`` will write. Overwritten by ``clean``; the stored config
        # is the right answer for a form that is never validated at all.
        self.policy_config = self.instance.config

    def _sections_for_instance(self) -> list[str]:
        """The checkers to render, or none at all for a node with no row yet.

        ``sections_for`` reads ``node.alerts``, and a reverse relation on an
        unsaved instance raises. ``NodeAdmin`` forbids adding nodes so this
        should not happen, but a form that explodes when constructed bare is a
        trap for whoever wires it up next.
        """
        if self.instance.pk is None:
            return []
        return sections_for(self.instance)

    @staticmethod
    def _build_field(field) -> forms.Field:
        if field.kind == "number":
            return forms.FloatField(required=False, label=field.label, help_text=field.help_text)
        # ``empty_value=None`` so a cleared box reaches ``to_config`` as "remove
        # this key". An empty allowlist is a real policy to the scorer, but it
        # scores the same as the checker's own default, whereas without this an
        # operator would have no way at all to take an allowlist back off.
        return forms.CharField(
            required=False, empty_value=None, label=field.label, help_text=field.help_text
        )

    def _clean_thresholds(self, checker: str) -> None:
        """Enforce the pair rule, on the box that is wrong.

        ``_score_numeric`` needs both thresholds and refuses an inverted pair, so
        a half-filled or inverted policy saves cleanly today and then does
        nothing. The blame lands on the missing box, or on the critical one when
        both are present.
        """
        warning = self.cleaned_data.get(field_name(checker, "warning_threshold"))
        critical = self.cleaned_data.get(field_name(checker, "critical_threshold"))
        try:
            clean_thresholds(warning, critical)
        except PolicyError as exc:
            culprit = "warning_threshold" if warning is None else "critical_threshold"
            self.add_error(field_name(checker, culprit), forms.ValidationError(str(exc)))

    def _clean_allowlist(self, checker: str) -> None:
        key = field_name(checker, "allowlist")
        value = self.cleaned_data.get(key)
        if value is None:
            return
        try:
            clean_int_list(value)
        except PolicyError as exc:
            self.add_error(key, forms.ValidationError(str(exc)))

    def clean(self):
        cleaned = super().clean()
        for checker in self._sections:
            kinds = {field.kind for field in spec_for(checker)}
            if "number" in kinds:
                self._clean_thresholds(checker)
            if "int_list" in kinds:
                self._clean_allowlist(checker)
        if self.errors:
            # A field that failed validation is gone from ``cleaned_data``, and
            # reading it as ``None`` would assemble a config full of deletions
            # nobody asked for. An invalid form writes nothing, so it keeps the
            # stored config as its answer.
            return cleaned
        # Assembled here, applied in ``save``: validation must not mutate the
        # instance, or a caller can no longer compare the stored config against
        # the one this form would write.
        self.policy_config = to_config(
            {name: cleaned.get(name) for name in self.fields}, existing=self.instance.config
        )
        return cleaned

    def save(self, commit=True):
        # Set before ``super().save`` so ``commit=False`` (which the admin uses)
        # still hands back an instance carrying the new policy.
        self.instance.config = self.policy_config
        return super().save(commit=commit)
