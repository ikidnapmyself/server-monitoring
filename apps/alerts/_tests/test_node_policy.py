from django.test import TestCase

from apps.alerts.node_policy import (
    FIELD_SPECS,
    PolicyError,
    PolicyField,
    clean_int_list,
    clean_number,
    clean_thresholds,
    field_name,
    spec_for,
    to_config,
    to_form_values,
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


class RoundTripTests(TestCase):
    def test_a_config_becomes_flat_form_values(self):
        config = {"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        self.assertEqual(
            to_form_values(config),
            {"policy__cpu__warning_threshold": 80, "policy__cpu__critical_threshold": 95},
        )

    def test_an_allowlist_renders_comma_separated(self):
        config = {"listening_ports": {"allowlist": [22, 443]}}
        self.assertEqual(to_form_values(config)["policy__listening_ports__allowlist"], "22, 443")

    def test_form_values_become_a_config(self):
        values = {"policy__cpu__warning_threshold": 80.0, "policy__cpu__critical_threshold": 95.0}
        self.assertEqual(
            to_config(values, existing={}),
            {"cpu": {"warning_threshold": 80.0, "critical_threshold": 95.0}},
        )

    def test_unknown_keys_survive_a_save(self):
        # Nothing an operator authored is ever silently deleted.
        existing = {"cpu": {"warning_threshold": 80}, "made_up": {"anything": 1}}
        result = to_config({"policy__cpu__warning_threshold": 99.0}, existing=existing)
        self.assertEqual(result["made_up"], {"anything": 1})

    def test_an_unknown_key_inside_a_known_checker_survives(self):
        existing = {"cpu": {"warning_threshold": 80, "future_option": "x"}}
        result = to_config({"policy__cpu__warning_threshold": 99.0}, existing=existing)
        self.assertEqual(result["cpu"]["future_option"], "x")
        self.assertEqual(result["cpu"]["warning_threshold"], 99.0)

    def test_a_config_with_no_edits_round_trips_unchanged(self):
        config = {
            "cpu": {"warning_threshold": 80, "critical_threshold": 95},
            "listening_ports": {"allowlist": [22]},
            "made_up": {"anything": 1},
        }
        self.assertEqual(to_config(to_form_values(config), existing=config), config)

    def test_an_untouched_int_threshold_stays_an_int(self):
        # Task 9 asks "did anything scoring-relevant change?". An int that comes
        # back a float on an untouched save is a spurious yes.
        config = {"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        result = to_config(
            {"policy__cpu__warning_threshold": 80.0, "policy__cpu__critical_threshold": 95.0},
            existing=config,
        )
        self.assertIsInstance(result["cpu"]["warning_threshold"], int)

    def test_clearing_a_field_removes_it(self):
        existing = {"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        result = to_config(
            {"policy__cpu__warning_threshold": None, "policy__cpu__critical_threshold": None},
            existing=existing,
        )
        self.assertNotIn("warning_threshold", result.get("cpu", {}))

    def test_an_emptied_checker_keeps_its_key(self):
        # `{"cpu": {}}` is how a checker says "I am configured here, showing
        # nothing"; dropping the key would drop its section out of the form.
        existing = {"cpu": {"warning_threshold": 80}}
        result = to_config({"policy__cpu__warning_threshold": None}, existing=existing)
        self.assertEqual(result["cpu"], {})

    def test_an_untouched_checker_is_not_given_an_empty_entry(self):
        # No key in `values` means the form never rendered that checker.
        self.assertEqual(to_config({}, existing={}), {})

    def test_a_non_dict_config_does_not_crash(self):
        # Node.config is a JSONField; nothing stops a string being written to it.
        self.assertEqual(to_form_values("not-a-dict"), {})

    def test_a_non_dict_checker_entry_does_not_crash(self):
        self.assertEqual(to_form_values({"cpu": "oops"}), {})

    def test_a_non_list_allowlist_reads_as_blank(self):
        config = {"listening_ports": {"allowlist": "oops"}}
        self.assertEqual(to_form_values(config)["policy__listening_ports__allowlist"], "")

    def test_a_non_dict_existing_does_not_crash(self):
        result = to_config({"policy__cpu__warning_threshold": 90.0}, existing="not-a-dict")
        self.assertEqual(result, {"cpu": {"warning_threshold": 90.0}})

    def test_a_non_dict_existing_entry_is_replaced_on_edit(self):
        result = to_config({"policy__cpu__warning_threshold": 90.0}, existing={"cpu": "oops"})
        self.assertEqual(result["cpu"], {"warning_threshold": 90.0})

    def test_an_allowlist_is_parsed_back_into_ports(self):
        result = to_config({"policy__listening_ports__allowlist": "22, 443"}, existing={})
        self.assertEqual(result["listening_ports"], {"allowlist": [22, 443]})

    def test_field_names_are_stable(self):
        self.assertEqual(field_name("cpu", "warning_threshold"), "policy__cpu__warning_threshold")
