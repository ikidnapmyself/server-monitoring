from django.test import TestCase

from apps.alerts.node_policy import (
    FIELD_SPECS,
    PolicyError,
    PolicyField,
    clean_int_list,
    clean_number,
    clean_thresholds,
    spec_for,
)
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


class ValidationTests(TestCase):
    def test_a_threshold_must_be_a_number(self):
        with self.assertRaises(PolicyError):
            clean_number("not-a-number")

    def test_a_bool_is_not_a_number(self):
        # bool is an int subclass; reevaluation._number rejects it and so must we.
        with self.assertRaises(PolicyError):
            clean_number(True)

    def test_a_number_comes_back_as_a_float(self):
        self.assertEqual(clean_number(90), 90.0)

    def test_critical_below_warning_is_rejected(self):
        # _score_numeric treats an inverted pair as malformed and passes through,
        # so today this saves cleanly and then does nothing. That is the bug.
        with self.assertRaises(PolicyError):
            clean_thresholds(warning=90.0, critical=80.0)

    def test_equal_thresholds_are_allowed(self):
        # _score_numeric only rejects crit < warn, so equal is a valid policy.
        self.assertEqual(clean_thresholds(warning=90.0, critical=90.0), (90.0, 90.0))

    def test_both_thresholds_blank_is_no_policy(self):
        self.assertIsNone(clean_thresholds(warning=None, critical=None))

    def test_only_a_warning_is_rejected(self):
        # _score_numeric returns None when either threshold is missing, so a
        # half-filled policy is another silent no-op.
        with self.assertRaises(PolicyError):
            clean_thresholds(warning=90.0, critical=None)

    def test_only_a_critical_is_rejected(self):
        with self.assertRaises(PolicyError):
            clean_thresholds(warning=None, critical=90.0)

    def test_a_bad_threshold_is_reported_against_its_own_field(self):
        with self.assertRaises(PolicyError):
            clean_thresholds(warning="x", critical=90.0)
        with self.assertRaises(PolicyError):
            clean_thresholds(warning=90.0, critical="x")

    def test_an_allowlist_parses_comma_separated_ports(self):
        self.assertEqual(clean_int_list("22, 443,8080"), [22, 443, 8080])

    def test_an_empty_allowlist_is_an_empty_list_not_none(self):
        # An empty allowlist is meaningful to _score_allowlist: it means
        # "flag only externally-exposed ports". It is not the same as no policy.
        self.assertEqual(clean_int_list(""), [])
        self.assertEqual(clean_int_list("  ,  "), [])

    def test_a_non_integer_port_is_rejected(self):
        with self.assertRaises(PolicyError):
            clean_int_list("22, http")

    def test_a_port_out_of_range_is_rejected(self):
        with self.assertRaises(PolicyError):
            clean_int_list("70000")
        with self.assertRaises(PolicyError):
            clean_int_list("0")

    def test_errors_say_what_to_fix(self):
        with self.assertRaises(PolicyError) as ctx:
            clean_thresholds(warning=90.0, critical=80.0)
        self.assertIn("critical", str(ctx.exception).lower())
        self.assertIn("warning", str(ctx.exception).lower())
