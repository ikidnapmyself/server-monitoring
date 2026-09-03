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
    build_policy_overview,
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


class RowIsProblemTests(PolicyOverviewTestCase):
    def test_a_scoring_row_is_not_a_problem(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        (row,) = rows_for_node(node)
        self.assertFalse(row.is_problem)

    def test_a_not_scoring_row_is_a_problem(self):
        node = self._node({"memory": {"warning_threshold": 90}})
        (row,) = rows_for_node(node)
        self.assertTrue(row.is_problem)

    def test_a_not_honoured_row_is_a_problem(self):
        node = self._node({"network": {"warning_threshold": 60}})
        (row,) = rows_for_node(node)
        self.assertTrue(row.is_problem)


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


class BuildPolicyOverviewTests(TestCase):
    HEALTHY = {"warning_threshold": 1, "critical_threshold": 2}

    def _node_named(self, instance_id, config):
        return Node.objects.create(instance_id=instance_id, config=config)

    def test_a_node_with_a_problem_sorts_above_a_healthy_one(self):
        self._node_named("a-healthy", {"cpu": {"warning_threshold": 1, "critical_threshold": 2}})
        self._node_named("z-broken", {"cpu": {"warning_threshold": 1}})
        overview = build_policy_overview()
        self.assertEqual([g.instance_id for g in overview.groups], ["z-broken", "a-healthy"])

    def test_healthy_nodes_sort_among_themselves_by_instance_id(self):
        for name in ["b", "a"]:
            self._node_named(name, {"cpu": {"warning_threshold": 1, "critical_threshold": 2}})
        self.assertEqual([g.instance_id for g in build_policy_overview().groups], ["a", "b"])

    def test_a_node_with_no_policy_is_counted_not_listed(self):
        self._node_named("configured", {"cpu": {"warning_threshold": 1, "critical_threshold": 2}})
        self._node_named("quiet", {})
        self._node_named("marker-only", {"cpu": {}})
        overview = build_policy_overview()
        self.assertEqual([g.instance_id for g in overview.groups], ["configured"])
        self.assertEqual(overview.quiet_count, 2)

    def test_an_empty_hub_reads_as_nothing_configured(self):
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 0)

    def test_a_group_carries_the_hostname_and_its_own_link(self):
        node = Node.objects.create(
            instance_id="a", hostname="a.local", config={"cpu": {"warning_threshold": 1}}
        )
        (group,) = build_policy_overview().groups
        self.assertEqual(group.hostname, "a.local")
        self.assertEqual(group.node_url, f"/admin/alerts/node/{node.pk}/change/")

    def test_one_broken_checker_among_healthy_ones_makes_the_node_a_problem(self):
        self._node_named("a", {"cpu": self.HEALTHY, "memory": {"warning_threshold": 1}})
        (group,) = build_policy_overview().groups
        self.assertEqual([row.status for row in group.rows], [IN_EFFECT, NOT_SCORING])
        self.assertTrue(group.has_problem)

    def test_a_node_whose_only_problem_is_an_unread_key_still_counts_as_broken(self):
        self._node_named("a", {"cpu": dict(self.HEALTHY, spare=1)})
        (group,) = build_policy_overview().groups
        self.assertEqual([row.status for row in group.rows], [NOT_HONOURED])
        self.assertTrue(group.has_problem)

    def test_problem_nodes_sort_among_themselves_by_instance_id(self):
        for name in ["b", "a"]:
            self._node_named(name, {"cpu": {"warning_threshold": 1}})
        self.assertEqual([g.instance_id for g in build_policy_overview().groups], ["a", "b"])

    def test_a_node_whose_config_is_not_a_mapping_does_not_hide_the_rest(self):
        self._node_named("healthy", {"cpu": self.HEALTHY})
        self._node_named("poisoned", "not a dict")
        overview = build_policy_overview()
        self.assertEqual([g.instance_id for g in overview.groups], ["healthy"])
        self.assertEqual(overview.quiet_count, 1)
