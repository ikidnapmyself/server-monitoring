"""Rows for the hub-side policy overview page.

Every case here is a shape ``Node.config`` can actually hold, because the ingest
path never validates it: a policy that scores, one with the right keys and an
unusable value, one whose keys nothing reads, and a checker that is both at once.
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


class RowsForNodeTests(TestCase):
    def _node(self, config):
        return Node.objects.create(instance_id="node-a", hostname="a", config=config)

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
        self.assertEqual(row.why, "Set a critical threshold too, or clear both.")

    def test_a_checker_no_scorer_reads_is_one_not_honoured_row(self):
        # No scorer reads network, so the whole entry is named, not its keys.
        node = self._node({"network": {"warning_threshold": 60}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.checker, "network")
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, NO_POLICY)
        self.assertEqual(row.why, "Nothing reads network.")

    def test_a_scoring_checker_with_a_leftover_key_stays_one_row(self):
        # One decision an operator made, so one row. The ignored key rides along
        # in the reason rather than splitting into a second, contradictory row.
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99, "spare": 1}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, "Warning at 90, Critical at 99")
        self.assertEqual(row.why, "Nothing reads cpu → spare.")

    def test_an_editor_note_is_reported_on_a_row_that_scores(self):
        # 70000 is not a port the boxes accept, but _int_set coerces it, so it
        # really is in effect and cannot be retyped.
        node = self._node({"listening_ports": {"allowlist": [70000]}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, IN_EFFECT)
        self.assertIn("stricter than the scorers", row.why)

    def test_the_empty_section_marker_makes_no_row(self):
        # {"cpu": {}} is what opens a section in the form. It scores nothing and
        # holds no key for anything to ignore.
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


class RowLinkTests(TestCase):
    def test_an_editable_row_links_to_that_checkers_own_box(self):
        node = Node.objects.create(
            instance_id="node-a",
            config={"cpu": {"warning_threshold": 90, "critical_threshold": 99}},
        )
        (row,) = rows_for_node(node)
        self.assertTrue(row.edit_url.startswith(row.node_url))
        self.assertTrue(row.edit_url.endswith("#id_policy__cpu__warning_threshold"))

    def test_a_row_with_no_boxes_links_to_the_page_with_no_fragment(self):
        node = Node.objects.create(
            instance_id="node-a", config={"network": {"warning_threshold": 60}}
        )
        (row,) = rows_for_node(node)
        self.assertEqual(row.edit_url, row.node_url)
