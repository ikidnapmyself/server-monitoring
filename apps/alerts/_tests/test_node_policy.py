from django.test import TestCase

from apps.alerts.node_policy import FIELD_SPECS, PolicyField, spec_for
from apps.alerts.reevaluation import PRIMARY_METRIC, SCORERS


class FieldSpecTests(TestCase):
    def test_every_configurable_checker_has_a_spec(self):
        # SCORERS is the authority on what accepts policy. If it grows, the form
        # must grow with it without anyone editing the form.
        self.assertEqual(set(FIELD_SPECS), set(SCORERS))

    def test_numeric_checkers_take_two_thresholds(self):
        fields = spec_for("cpu")
        self.assertEqual([f.name for f in fields], ["warning_threshold", "critical_threshold"])
        self.assertTrue(all(f.kind == "number" for f in fields))

    def test_listening_ports_takes_an_allowlist(self):
        fields = spec_for("listening_ports")
        self.assertEqual([f.name for f in fields], ["allowlist"])
        self.assertEqual(fields[0].kind, "int_list")

    def test_every_numeric_checker_in_primary_metric_is_covered(self):
        for checker in PRIMARY_METRIC:
            self.assertEqual(
                [f.name for f in spec_for(checker)],
                ["warning_threshold", "critical_threshold"],
            )

    def test_a_checker_with_no_policy_has_no_spec(self):
        self.assertEqual(spec_for("raid"), [])

    def test_fields_are_frozen_and_carry_operator_text(self):
        field = PolicyField(name="x", kind="number", label="X", help_text="why")
        self.assertEqual(
            (field.name, field.kind, field.label, field.help_text), ("x", "number", "X", "why")
        )
        for fields in FIELD_SPECS.values():
            for spec in fields:
                self.assertTrue(spec.label and spec.help_text)
