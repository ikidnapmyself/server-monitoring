"""Rows for the hub-side policy overview page.

Every case here is a shape ``Node.config`` can actually hold, because the ingest
path never validates it.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Node
from apps.alerts.policy_overview import (
    IN_EFFECT,
    NO_POLICY,
    NO_POLICY_SET,
    NOT_HONOURED,
    NOT_REEVALUATABLE,
    NOT_SCORING,
    build_policy_overview,
    rows_for_node,
)


class PolicyOverviewTestCase(TestCase):
    def _node(self, config):
        return Node.objects.create(instance_id="node-a", hostname="a", config=config)

    def _change_url(self, node):
        return reverse("admin:alerts_node_change", args=[node.pk])


class RowsForNodeTests(PolicyOverviewTestCase):
    def test_a_scoring_policy_is_one_row_in_effect(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.checker, "cpu")
        self.assertEqual(row.status, IN_EFFECT)
        self.assertEqual(row.policy, "Warning at 90, Critical at 99")
        self.assertEqual(row.why, "")
        self.assertFalse(row.is_problem)
        self.assertFalse(row.caution)

    def test_a_half_filled_threshold_pair_is_not_scoring_with_the_forms_own_reason(self):
        node = self._node({"memory": {"warning_threshold": 90}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.status, NOT_SCORING)
        self.assertEqual(row.policy, "Warning at 90")
        self.assertEqual(row.why, "Set a critical threshold too, or clear both.")
        self.assertTrue(row.is_problem)

    def test_a_checker_no_scorer_reads_is_one_not_honoured_row(self):
        node = self._node({"network": {"warning_threshold": 60}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.checker, "network")
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.policy, NO_POLICY)
        self.assertEqual(row.why, "Nothing reads network.")
        self.assertTrue(row.is_problem)

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
        self.assertFalse(row.is_problem)
        self.assertTrue(row.caution)
        self.assertTrue(row.why.startswith("Scoring as stored, but "))
        self.assertIn("stricter than the scorers", row.why)
        self.assertTrue(row.why.endswith("Retyping it on the node page means changing it."))

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
        self.assertEqual(row.edit_url, self._change_url(node))


class RowLinkTests(PolicyOverviewTestCase):
    def test_an_editable_row_links_to_that_checkers_own_box(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        (row,) = rows_for_node(node)
        self.assertEqual(
            row.edit_url, f"{self._change_url(node)}#id_policy__cpu__warning_threshold"
        )

    def test_a_row_with_no_boxes_links_to_the_page_with_no_fragment(self):
        node = self._node({"network": {"warning_threshold": 60}})
        (row,) = rows_for_node(node)
        self.assertEqual(row.edit_url, self._change_url(node))


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

    def test_a_cautioned_row_leaves_its_node_healthy(self):
        self._node_named("a", {"listening_ports": {"allowlist": [70000]}})
        (group,) = build_policy_overview().groups
        self.assertEqual([row.caution for row in group.rows], [True])
        self.assertFalse(group.has_problem)

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


class FiringAlertRowTests(PolicyOverviewTestCase):
    def _alert(self, checker, instance_id="node-a", status="firing", labels=None):
        return Alert.objects.create(
            fingerprint=f"check:{instance_id}:{checker}",
            source="cluster",
            name=f"{checker} high",
            severity="critical",
            status=status,
            started_at=timezone.now(),
            labels={"checker": checker, "instance_id": instance_id} if labels is None else labels,
        )

    def test_a_firing_checker_with_no_config_entry_is_a_no_policy_set_row(self):
        node = self._node({})
        self._alert("disk")
        (row,) = rows_for_node(node, {"disk"})
        self.assertEqual(row.checker, "disk")
        self.assertEqual(row.status, NO_POLICY_SET)
        self.assertEqual(row.policy, NO_POLICY)
        self.assertEqual(
            row.why, "Alerting now with no policy set, so disk scores as the node sends it."
        )
        self.assertTrue(row.is_problem)
        self.assertFalse(row.is_muted)

    def test_a_no_policy_set_row_links_to_that_checkers_own_box(self):
        node = self._node({})
        (row,) = rows_for_node(node, {"disk"})
        self.assertEqual(
            row.edit_url, f"{self._change_url(node)}#id_policy__disk__warning_threshold"
        )

    def test_a_firing_checker_no_scorer_reads_is_a_not_reevaluatable_row(self):
        node = self._node({})
        (row,) = rows_for_node(node, {"raid"})
        self.assertEqual(row.checker, "raid")
        self.assertEqual(row.status, NOT_REEVALUATABLE)
        self.assertEqual(row.policy, NO_POLICY)
        self.assertEqual(
            row.why, "Alerting now, but no scorer reads raid, so no policy can change it."
        )
        self.assertFalse(row.is_problem)
        self.assertTrue(row.is_muted)

    def test_a_not_reevaluatable_row_links_to_the_page_with_no_fragment(self):
        node = self._node({})
        (row,) = rows_for_node(node, {"raid"})
        self.assertEqual(row.edit_url, self._change_url(node))

    def test_a_per_mount_disk_checker_is_not_reevaluatable(self):
        (row,) = rows_for_node(self._node({}), {"disk:/var"})
        self.assertEqual(row.status, NOT_REEVALUATABLE)

    def test_a_checker_with_both_config_and_a_firing_alert_is_one_row(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        (row,) = rows_for_node(node, {"cpu"})
        self.assertEqual(row.status, IN_EFFECT)
        self.assertEqual(row.policy, "Warning at 90, Critical at 99")

    def test_a_firing_alert_for_an_unread_config_entry_is_one_not_honoured_row(self):
        node = self._node({"network": {"warning_threshold": 60}})
        (row,) = rows_for_node(node, {"network"})
        self.assertEqual(row.status, NOT_HONOURED)
        self.assertEqual(row.why, "Nothing reads network.")

    def test_not_reevaluatable_rows_sort_last_and_the_rest_stay_alphabetical(self):
        node = self._node({"memory": {"warning_threshold": 1, "critical_threshold": 2}})
        rows = rows_for_node(node, {"raid", "cpu"})
        self.assertEqual([row.checker for row in rows], ["cpu", "memory", "raid"])

    def test_a_resolved_alert_makes_no_row(self):
        node = self._node({})
        self._alert("disk", status="resolved")
        self.assertEqual(rows_for_node(node), [])
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 1)

    def test_an_alert_with_no_checker_label_makes_no_row(self):
        self._node({})
        self._alert("disk", labels={"instance_id": "node-a"})
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 1)

    def test_an_alert_with_no_instance_id_label_makes_no_row(self):
        self._node({})
        self._alert("disk", labels={"checker": "disk"})
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 1)

    def test_an_alert_whose_checker_label_is_not_a_string_makes_no_row(self):
        self._node({})
        self._alert("disk", labels={"checker": ["disk"], "instance_id": "node-a"})
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 1)

    def test_an_alert_is_matched_to_its_node_by_label_not_by_the_node_fk(self):
        # The FK is stamped at alert creation, so an alert raised before its node
        # registered is unlinked while still belonging to that node.
        self._node({})
        alert = self._alert("disk")
        self.assertIsNone(alert.node)
        (group,) = build_policy_overview().groups
        self.assertEqual([row.status for row in group.rows], [NO_POLICY_SET])

    def test_a_firing_alert_on_an_unknown_instance_id_lists_no_node(self):
        self._node({})
        self._alert("disk", instance_id="not-a-node")
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 1)

    def test_a_no_policy_set_row_makes_its_node_a_problem_node(self):
        self._node({})
        self._alert("disk")
        (group,) = build_policy_overview().groups
        self.assertTrue(group.has_problem)

    def test_a_not_reevaluatable_row_does_not_make_its_node_a_problem_node(self):
        self._node({})
        self._alert("raid")
        (group,) = build_policy_overview().groups
        self.assertEqual([row.status for row in group.rows], [NOT_REEVALUATABLE])
        self.assertFalse(group.has_problem)

    def test_a_node_with_neither_config_nor_alerts_is_only_counted(self):
        self._node({})
        overview = build_policy_overview()
        self.assertEqual(overview.groups, [])
        self.assertEqual(overview.quiet_count, 1)


class QueryCountTests(TestCase):
    def _populate(self, start, stop):
        for index in range(start, stop):
            instance_id = f"node-{index}"
            Node.objects.create(
                instance_id=instance_id,
                config={"cpu": {"warning_threshold": 1, "critical_threshold": 2}},
            )
            for checker in ["disk", "raid"]:
                Alert.objects.create(
                    fingerprint=f"check:{instance_id}:{checker}",
                    source="cluster",
                    name=f"{checker} high",
                    severity="critical",
                    status="firing",
                    started_at=timezone.now(),
                    labels={"checker": checker, "instance_id": instance_id},
                )

    def test_the_page_builds_in_two_queries_however_many_nodes_there_are(self):
        self._populate(0, 2)
        with self.assertNumQueries(2):
            self.assertEqual(len(build_policy_overview().groups), 2)
        self._populate(2, 8)
        with self.assertNumQueries(2):
            self.assertEqual(len(build_policy_overview().groups), 8)
