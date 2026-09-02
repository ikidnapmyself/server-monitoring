"""The policy form: the strict half of a fail-open runtime.

``apps.alerts.reevaluation`` silently ignores a policy it cannot use, so every
test here is about an operator being told at the keyboard what the ingest path
would never tell them.
"""

from django.test import TestCase

from apps.alerts.forms import NodePolicyForm
from apps.alerts.models import Node


class NodePolicyFormTests(TestCase):
    def _node(self, **kwargs):
        kwargs.setdefault("instance_id", "web-03")
        kwargs.setdefault("hostname", "web-03")
        return Node.objects.create(**kwargs)

    def test_fields_are_built_for_the_nodes_sections(self):
        node = self._node(
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}},
        )
        form = NodePolicyForm(instance=node)
        self.assertIn("policy__cpu__warning_threshold", form.fields)
        self.assertEqual(form.initial["policy__cpu__warning_threshold"], 80)

    def test_fields_carry_the_specs_operator_text(self):
        node = self._node(config={"cpu": {"warning_threshold": 80}})
        field = NodePolicyForm(instance=node).fields["policy__cpu__critical_threshold"]
        self.assertEqual(field.label, "Critical at")
        self.assertIn("warning", field.help_text)
        self.assertFalse(field.required)

    def test_the_raw_config_field_is_not_editable(self):
        node = self._node()
        self.assertNotIn("config", NodePolicyForm(instance=node).fields)

    def test_a_node_with_no_sections_has_no_policy_fields_and_still_validates(self):
        node = self._node()
        form = NodePolicyForm(instance=node, data={})
        self.assertEqual(form.fields, {})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().config, {})

    def test_an_unsaved_instance_cannot_raise(self):
        # NodeAdmin forbids adding nodes, but a form built with no instance at
        # all must not explode on a reverse relation that has no row to follow.
        form = NodePolicyForm()
        self.assertEqual(form.fields, {})
        self.assertTrue(NodePolicyForm(data={}).is_valid())

    def test_an_inverted_pair_is_a_field_error_not_a_silent_save(self):
        node = self._node(config={"cpu": {"warning_threshold": 1}})
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "90",
                "policy__cpu__critical_threshold": "80",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("policy__cpu__critical_threshold", form.errors)
        self.assertIn("critical_threshold", str(form.errors))
        self.assertNotIn("__all__", form.errors)

    def test_a_missing_critical_is_a_field_error_on_the_critical_box(self):
        node = self._node(config={"cpu": {"warning_threshold": 1}})
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "90",
                "policy__cpu__critical_threshold": "",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("policy__cpu__critical_threshold", form.errors)

    def test_a_missing_warning_is_a_field_error_on_the_warning_box(self):
        node = self._node(config={"cpu": {"warning_threshold": 1}})
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "",
                "policy__cpu__critical_threshold": "90",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("policy__cpu__warning_threshold", form.errors)

    def test_a_valid_save_writes_the_config(self):
        node = self._node(config={"cpu": {"warning_threshold": 1}})
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "70",
                "policy__cpu__critical_threshold": "90",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        node.refresh_from_db()
        self.assertEqual(
            node.config, {"cpu": {"warning_threshold": 70.0, "critical_threshold": 90.0}}
        )

    def test_clearing_both_thresholds_removes_them(self):
        node = self._node(config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}})
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "",
                "policy__cpu__critical_threshold": "",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        node.refresh_from_db()
        self.assertEqual(node.config, {"cpu": {}})

    def test_saving_an_untouched_form_leaves_config_byte_identical(self):
        # Every operator who opens a node to look at it and hits Save out of
        # habit. Task 9 asks "did anything change?" and must hear no.
        config = {
            "cpu": {"warning_threshold": 80, "critical_threshold": 95},
            "listening_ports": {"allowlist": [22, 443]},
            "made_up": {"anything": 1},
        }
        node = self._node(config=config)
        unbound = NodePolicyForm(instance=node)
        data = {name: unbound.initial[name] for name in unbound.fields}
        form = NodePolicyForm(instance=node, data=data)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        node.refresh_from_db()
        self.assertEqual(node.config, config)
        self.assertIsInstance(node.config["cpu"]["warning_threshold"], int)
        self.assertEqual(node.config["listening_ports"]["allowlist"], [22, 443])

    def test_an_allowlist_round_trips_through_the_text_field(self):
        node = self._node(config={"listening_ports": {"allowlist": [22]}})
        form = NodePolicyForm(instance=node)
        self.assertEqual(form.initial["policy__listening_ports__allowlist"], "22")
        form = NodePolicyForm(instance=node, data={"policy__listening_ports__allowlist": "22, 443"})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        node.refresh_from_db()
        self.assertEqual(node.config["listening_ports"]["allowlist"], [22, 443])

    def test_a_bad_port_is_a_field_error(self):
        node = self._node(config={"listening_ports": {"allowlist": [22]}})
        form = NodePolicyForm(
            instance=node, data={"policy__listening_ports__allowlist": "22, http"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("policy__listening_ports__allowlist", form.errors)
        self.assertIn("http", str(form.errors))

    def test_clearing_an_allowlist_removes_the_policy(self):
        # Blank means "no allowlist policy", the only way an operator can undo
        # one. An empty allowlist scores the same as the checker's own default.
        node = self._node(config={"listening_ports": {"allowlist": [22]}})
        form = NodePolicyForm(instance=node, data={"policy__listening_ports__allowlist": ""})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        node.refresh_from_db()
        self.assertEqual(node.config, {"listening_ports": {}})

    def test_unknown_keys_survive_a_save_through_the_form(self):
        config = {"cpu": {"warning_threshold": 80, "future_option": "x"}, "made_up": {"a": 1}}
        node = self._node(config=config)
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "70",
                "policy__cpu__critical_threshold": "90",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        node.refresh_from_db()
        self.assertEqual(node.config["made_up"], {"a": 1})
        self.assertEqual(node.config["cpu"]["future_option"], "x")
        self.assertEqual(node.config["cpu"]["warning_threshold"], 70.0)

    def test_an_invalid_form_does_not_assemble_phantom_deletions(self):
        # A failed field is absent from cleaned_data; read as None it would look
        # like an operator clearing every other box.
        config = {"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        node = self._node(config=config)
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "nope",
                "policy__cpu__critical_threshold": "95",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.policy_config, config)

    def test_the_config_is_only_written_on_save(self):
        # Task 9 compares the stored config against what the form would write,
        # so validating must not already have mutated the instance.
        node = self._node(config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}})
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "70",
                "policy__cpu__critical_threshold": "90",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(node.config["cpu"]["warning_threshold"], 80)
        self.assertEqual(form.policy_config["cpu"]["warning_threshold"], 70.0)
        form.save(commit=False)
        self.assertEqual(form.instance.config["cpu"]["warning_threshold"], 70.0)


class StoredGarbageTests(TestCase):
    """A config the runtime silently ignores must edit as an error, not a 500."""

    def _node(self, config):
        return Node.objects.create(instance_id="web-03", hostname="web-03", config=config)

    def test_a_bool_threshold_renders_and_then_errors(self):
        node = self._node({"cpu": {"warning_threshold": True, "critical_threshold": 95}})
        unbound = NodePolicyForm(instance=node)
        self.assertEqual(unbound.initial["policy__cpu__warning_threshold"], True)
        self.assertIn("policy__cpu__warning_threshold", unbound.as_p())
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "True",
                "policy__cpu__critical_threshold": "95",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("policy__cpu__warning_threshold", form.errors)

    def test_a_non_numeric_string_threshold_errors(self):
        node = self._node({"cpu": {"warning_threshold": "high", "critical_threshold": "95"}})
        form = NodePolicyForm(
            instance=node,
            data={
                "policy__cpu__warning_threshold": "high",
                "policy__cpu__critical_threshold": "95",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("policy__cpu__warning_threshold", form.errors)
        self.assertNotIn("policy__cpu__critical_threshold", form.errors)

    def test_a_numeric_string_threshold_is_normalised_on_save(self):
        # "90" scores as nothing at ingest; opening and saving the node fixes it.
        node = self._node({"cpu": {"warning_threshold": "90", "critical_threshold": 95}})
        unbound = NodePolicyForm(instance=node)
        form = NodePolicyForm(
            instance=node, data={name: unbound.initial[name] for name in unbound.fields}
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        node.refresh_from_db()
        self.assertEqual(node.config["cpu"]["warning_threshold"], 90.0)
        self.assertEqual(node.config["cpu"]["critical_threshold"], 95)

    def test_a_non_dict_config_does_not_crash_the_form(self):
        node = self._node("not-a-dict")
        form = NodePolicyForm(instance=node, data={})
        self.assertEqual(form.fields, {})
        self.assertTrue(form.is_valid(), form.errors)
