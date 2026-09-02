from django.test import TestCase
from django.utils import timezone

from apps.alerts.identity import local_hostname, local_instance_id
from apps.alerts.models import Alert, AlertSeverity, Node
from apps.alerts.node_policy import (
    FIELD_SPECS,
    PolicyError,
    PolicyField,
    build_effective_policy,
    clean_int_list,
    clean_number,
    clean_thresholds,
    field_name,
    sections_for,
    spec_for,
    to_config,
    to_form_values,
)
from apps.alerts.reevaluation import PRIMARY_METRIC, SCORERS
from apps.checkers.checkers import CheckStatus
from apps.checkers.models import CheckRun


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


class SectionSelectionTests(TestCase):
    def _peer_alert(self, node, checker):
        return Alert.objects.create(
            fingerprint=f"check:{node.instance_id}:{checker}",
            source="cluster",
            name=checker,
            severity=AlertSeverity.WARNING,
            started_at=timezone.now(),
            node=node,
            labels={"checker": checker},
            annotations={},
        )

    def _check_run(self, hostname, checker):
        return CheckRun.objects.create(
            checker_name=checker,
            hostname=hostname,
            status=CheckStatus.OK.value,
            metrics={},
        )

    def test_a_node_shows_sections_for_the_checkers_it_reports(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._peer_alert(node, "cpu")
        self.assertEqual(sections_for(node), ["cpu"])

    def test_the_local_node_reports_through_its_check_runs(self):
        # A hub holds no Alert rows for its own checkers unless one fired, but it
        # does hold a CheckRun for every checker it has ever run.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._check_run("hub", "memory")
        self.assertEqual(sections_for(node), ["memory"])

    def test_the_local_node_with_no_recorded_hostname_falls_back_to_this_machine(self):
        # Node.upsert only writes hostname when truthy, so the local row can carry
        # the blank default; CheckRun rows are still keyed by the real hostname.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="")
        self._check_run(local_hostname(), "disk")
        self.assertEqual(sections_for(node), ["disk"])

    def test_a_local_node_reads_only_its_own_check_runs(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._check_run("somewhere-else", "cpu")
        self.assertEqual(sections_for(node), [])

    def test_a_configured_checker_shows_even_if_no_longer_reported(self):
        # Policy must never become invisible just because a checker went quiet.
        node = Node.objects.create(
            instance_id="web-03", hostname="web-03", config={"disk": {"warning_threshold": 90}}
        )
        self.assertEqual(sections_for(node), ["disk"])

    def test_reported_and_configured_are_unioned_without_duplicates(self):
        node = Node.objects.create(
            instance_id="web-03", hostname="web-03", config={"cpu": {"warning_threshold": 90}}
        )
        self._peer_alert(node, "cpu")
        self._peer_alert(node, "memory")
        self.assertEqual(sections_for(node), ["cpu", "memory"])

    def test_a_reported_checker_that_accepts_no_policy_is_omitted(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._peer_alert(node, "raid")  # not in SCORERS
        self.assertEqual(sections_for(node), [])

    def test_a_configured_checker_that_accepts_no_policy_is_omitted(self):
        # A stale key for a removed checker must not become an editable section.
        # Task 8 surfaces it read-only instead.
        node = Node.objects.create(
            instance_id="web-03", hostname="web-03", config={"made_up": {"anything": 1}}
        )
        self.assertEqual(sections_for(node), [])

    def test_a_node_with_nothing_reported_or_configured_shows_no_sections(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(sections_for(node), [])

    def test_sections_are_sorted(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        for name in ("memory", "cpu", "disk"):
            self._peer_alert(node, name)
        self.assertEqual(sections_for(node), ["cpu", "disk", "memory"])

    def test_a_webhook_alert_without_a_checker_label_is_not_a_section(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="webhook:1",
            source="grafana",
            name="something",
            severity=AlertSeverity.WARNING,
            started_at=timezone.now(),
            node=node,
            labels={"checker": ""},
            annotations={},
        )
        self.assertEqual(sections_for(node), [])

    def test_a_non_dict_label_payload_does_not_crash(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        alert = self._peer_alert(node, "cpu")
        Alert.objects.filter(pk=alert.pk).update(labels="not-a-dict")
        self.assertEqual(sections_for(node), [])

    def test_a_non_dict_config_does_not_crash(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        node.config = "not-a-dict"
        self.assertEqual(sections_for(node), [])


class EffectivePolicyTests(TestCase):
    """What is actually scored, and what nothing reads.

    ``to_config`` preserves every key it has no spec for, which is what makes a
    hand-written policy safe to edit. The cost is that a stale key is invisible,
    so this panel says it out loud instead.
    """

    def _node(self, config):
        return Node.objects.create(instance_id="web-03", hostname="web-03", config=config)

    def test_a_configured_checker_with_a_scorer_is_in_effect(self):
        policy = build_effective_policy(
            self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 95}})
        )
        self.assertEqual([s.checker for s in policy.sections], ["cpu"])
        self.assertEqual(
            [(v.label, v.value) for v in policy.sections[0].values],
            [("Warning at", "80"), ("Critical at", "95")],
        )
        self.assertEqual(policy.unread, [])

    def test_an_allowlist_reads_as_a_port_list(self):
        policy = build_effective_policy(self._node({"listening_ports": {"allowlist": [22, 80]}}))
        self.assertEqual([v.value for v in policy.sections[0].values], ["22, 80"])

    def test_an_unknown_checker_is_not_honoured_and_is_not_in_effect(self):
        policy = build_effective_policy(self._node({"made_up": {"warning_threshold": 1}}))
        self.assertEqual(policy.sections, [])
        self.assertEqual([(u.checker, u.key) for u in policy.unread], [("made_up", "")])

    def test_an_unknown_key_inside_a_known_checker_names_both(self):
        policy = build_effective_policy(
            self._node({"cpu": {"warning_threshold": 80, "sample_window": 5}})
        )
        self.assertEqual([s.checker for s in policy.sections], ["cpu"])
        self.assertEqual([v.value for v in policy.sections[0].values], ["80"])
        self.assertEqual([(u.checker, u.key) for u in policy.unread], [("cpu", "sample_window")])

    def test_an_empty_policy_is_not_claimed_to_be_in_effect(self):
        # {"cpu": {}} is the add-a-section marker. It scores nothing, so saying
        # it is in effect would be a lie.
        policy = build_effective_policy(self._node({"cpu": {}}))
        self.assertEqual(policy.sections, [])
        self.assertEqual(policy.unread, [])

    def test_a_node_with_no_config_has_nothing_either_way(self):
        policy = build_effective_policy(self._node({}))
        self.assertEqual(policy.sections, [])
        self.assertEqual(policy.unread, [])
        self.assertFalse(policy.has_content)

    def test_a_non_dict_config_does_not_crash(self):
        node = self._node({})
        node.config = "not-a-dict"
        policy = build_effective_policy(node)
        self.assertEqual((policy.sections, policy.unread), ([], []))

    def test_a_non_dict_entry_for_a_known_checker_does_not_crash(self):
        policy = build_effective_policy(self._node({"cpu": "90"}))
        self.assertEqual(policy.sections, [])
        self.assertEqual([(u.checker, u.key) for u in policy.unread], [("cpu", "")])

    def test_sections_and_unread_entries_are_sorted(self):
        policy = build_effective_policy(
            self._node(
                {
                    "memory": {"warning_threshold": 70, "critical_threshold": 90},
                    "cpu": {"warning_threshold": 80, "zzz": 1, "aaa": 2},
                    "zebra": {},
                }
            )
        )
        self.assertEqual([s.checker for s in policy.sections], ["cpu", "memory"])
        self.assertEqual(
            [(u.checker, u.key) for u in policy.unread],
            [("cpu", "aaa"), ("cpu", "zzz"), ("zebra", "")],
        )
        self.assertTrue(policy.has_content)

    def test_a_section_is_titled_for_a_human(self):
        policy = build_effective_policy(
            self._node({"listening_ports": {"allowlist": [22]}}),
        )
        self.assertEqual(policy.sections[0].title, "listening ports")
