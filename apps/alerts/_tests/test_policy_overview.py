"""Rows for the hub-side policy overview page.

Every case here is a shape ``Node.config`` can actually hold, because the ingest
path never validates it.
"""

from django.test import TestCase

from apps.alerts.models import Node
from apps.alerts.policy_overview import (
    IN_EFFECT,
    NO_POLICY,
    NOT_HONOURED,
    NOT_SCORING,
    rows_for_node,
)


class PolicyOverviewTestCase(TestCase):
    def _node(self, config):
        return Node.objects.create(instance_id="node-a", hostname="a", config=config)


class RowsForNodeTests(PolicyOverviewTestCase):
    def test_a_scoring_policy_is_one_row_in_effect(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.checker, "cpu")
        self.assertEqual(row.status, IN_EFFECT)
        self.assertEqual(row.policy, "Warning at 90, Critical at 99")
        self.assertEqual(row.why, "")

    def test_a_half_filled_threshold_pair_is_not_scoring_with_the_forms_own_reason(self):
        node = self._node({"memory": {"warning_threshold": 90}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_SCORING)
        self.assertEqual(row.policy, "Warning at 90")
        self.assertEqual(row.why, "Set a critical threshold too, or clear both.")

    def test_a_checker_no_scorer_reads_is_one_not_honoured_row(self):
        node = self._node({"network": {"warning_threshold": 60}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.checker, "network")
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, NO_POLICY)
        self.assertEqual(row.why, "Nothing reads network.")

    def test_a_scoring_checker_with_a_leftover_key_stays_one_row(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99, "spare": 1}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, "Warning at 90, Critical at 99")
        self.assertEqual(row.why, "Nothing reads cpu → spare.")

    def test_two_leftover_keys_on_one_checker_are_one_sentence(self):
        node = self._node(
            {"cpu": {"warning_threshold": 90, "critical_threshold": 99, "spare": 1, "old": 2}}
        )
        (row,) = rows_for_node(node)
        self.assertEqual(row.why, "Nothing reads cpu → old, cpu → spare.")

    def test_a_not_scoring_checker_with_a_leftover_key_keeps_the_worse_status(self):
        node = self._node({"memory": {"warning_threshold": 90, "spare": 1}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_SCORING)
        self.assertEqual(row.policy, "Warning at 90")
        self.assertEqual(
            row.why,
            "Set a critical threshold too, or clear both. Nothing reads memory → spare.",
        )

    def test_an_editor_note_is_reported_on_a_row_that_scores(self):
        # 70000 is not a port the boxes accept, but _int_set coerces it, so it
        # really is in effect and cannot be retyped.
        node = self._node({"listening_ports": {"allowlist": [70000]}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, IN_EFFECT)
        self.assertTrue(row.why.startswith("Scoring as stored, but "))
        self.assertIn("stricter than the scorers", row.why)

    def test_the_empty_section_marker_makes_no_row(self):
        # {"cpu": {}} is the marker that opens a section in the form.
        self.assertEqual(rows_for_node(self._node({"cpu": {}})), [])

    def test_no_config_makes_no_rows(self):
        self.assertEqual(rows_for_node(self._node({})), [])

    def test_rows_are_sorted_by_checker(self):
        node = self._node(
            {
                "memory": {"warning_threshold": 1, "critical_threshold": 2},
                "cpu": {"warning_threshold": 1, "critical_threshold": 2},
            }
        )
        self.assertEqual([row.checker for row in rows_for_node(node)], ["cpu", "memory"])


class NonMappingEntryTests(PolicyOverviewTestCase):
    def test_a_spec_d_checker_set_to_a_string_says_the_entry_is_wrong(self):
        node = self._node({"cpu": "90"})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, NO_POLICY)
        self.assertEqual(
            row.why, "cpu is set to something that is not a policy, so nothing reads it."
        )
        # cpu really does have boxes on the node page, so the link keeps its
        # fragment. The entry is what is wrong here, not the checker.
        self.assertTrue(row.edit_url.endswith("#id_policy__cpu__warning_threshold"))

    def test_a_checker_with_no_spec_set_to_a_string_says_nothing_reads_it(self):
        node = self._node({"network": "90"})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.why, "Nothing reads network.")
        self.assertEqual(row.edit_url, row.node_url)


class RowLinkTests(PolicyOverviewTestCase):
    def test_an_editable_row_links_to_that_checkers_own_box(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        (row,) = rows_for_node(node)
        self.assertTrue(row.edit_url.startswith(row.node_url))
        self.assertTrue(row.edit_url.endswith("#id_policy__cpu__warning_threshold"))

    def test_a_row_with_no_boxes_links_to_the_page_with_no_fragment(self):
        node = self._node({"network": {"warning_threshold": 60}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.edit_url, row.node_url)
