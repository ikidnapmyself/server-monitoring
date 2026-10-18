import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Node
from apps.alerts.policy_overview import build_policy_overview
from apps.checkers.checkers import checker_label
from apps.console.queries.policy import build_policy_page
from config.describing import Action, Fact

pytestmark = pytest.mark.django_db


def _firing(node, checker):
    return Alert.objects.create(
        fingerprint=f"check:{node.instance_id}:{checker}",
        source="cluster",
        name=checker,
        severity="critical",
        status="firing",
        started_at=timezone.now(),
        node=node,
        labels={"checker": checker, "instance_id": node.instance_id},
    )


def test_a_half_filled_pair_comes_before_a_healthy_node():
    Node.objects.create(
        instance_id="a-healthy",
        hostname="healthy",
        config={"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
    )
    Node.objects.create(
        instance_id="z-half", hostname="half", config={"cpu": {"warning_threshold": 80}}
    )

    page = build_policy_page()

    assert [group.name for group in page.groups] == ["half", "healthy"]
    assert [group.has_problem for group in page.groups] == [True, False]


def test_rows_read_in_labels_with_the_keys_kept_for_details():
    Node.objects.create(
        instance_id="i-web",
        hostname="web",
        config={"listening_ports": {"allowlist": [22], "extra_key": 1}},
    )

    (row,) = build_policy_page().groups[0].rows

    assert row.checker == checker_label("listening_ports")
    assert row.key == "listening_ports"
    assert row.unread == (Fact("Stored key", "listening_ports → extra_key"),)


def test_an_unnamed_node_keeps_its_id_in_details():
    node = Node.objects.create(instance_id="i-anon", config={"cpu": {"warning_threshold": 80}})

    (group,) = build_policy_page().groups

    assert group.name == "Unnamed node"
    assert group.pk == node.pk
    assert group.view == Action("Open the node", "view_node", {"pk": node.pk})
    assert group.facts == (Fact("Instance ID", "i-anon", copy=True),)


def test_each_row_carries_its_policy_rows_actions():
    node = Node.objects.create(
        instance_id="i-web",
        hostname="web",
        config={"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
    )
    _firing(node, "cpu")

    (row,) = build_policy_page().groups[0].rows
    (source,) = build_policy_overview().groups[0].rows

    assert (row.edit, row.reeval) == (source.edit, source.reeval)
    assert row.reeval is not None
    assert (row.policy, row.status, row.tone, row.why) == (
        source.policy,
        source.status,
        source.tone,
        source.why,
    )
    assert (row.effect, row.applied, row.caution) == (source.effect, source.applied, False)


def test_quiet_nodes_are_counted_not_listed():
    Node.objects.create(instance_id="i-quiet", hostname="quiet")
    Node.objects.create(instance_id="i-also", hostname="also")

    page = build_policy_page()

    assert page.groups == ()
    assert page.quiet_count == 2
