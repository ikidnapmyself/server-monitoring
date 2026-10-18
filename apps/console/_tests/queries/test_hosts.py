import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.queries.hosts import (
    alert_host,
    host_summary,
    incident_hosts,
    node_choices,
    on_node,
)


def _alert(incident, fingerprint, **fields):
    return Alert.objects.create(
        incident=incident,
        fingerprint=fingerprint,
        source="test",
        name=fingerprint,
        started_at=timezone.now(),
        **fields,
    )


@pytest.mark.django_db
def test_a_linked_node_names_the_host_by_hostname_then_instance_id():
    incident = Incident.objects.create()
    _alert(incident, "a", node=Node.objects.create(instance_id="i-1", hostname="web-1"))
    _alert(incident, "b", node=Node.objects.create(instance_id="i-2", hostname=""))

    assert incident_hosts([incident.pk]) == {incident.pk: ("i-2", "web-1")}


@pytest.mark.django_db
def test_without_a_node_the_host_comes_from_the_labels():
    incident = Incident.objects.create()
    _alert(incident, "a", labels={"instance_id": "db-1"})
    _alert(incident, "b", labels={"instance": "Cache-1:9100"})
    _alert(incident, "c", labels={"hostname": "app-1"})

    assert incident_hosts([incident.pk]) == {incident.pk: ("app-1", "Cache-1:9100", "db-1")}


@pytest.mark.django_db
def test_hosts_are_deduplicated_and_unknown_or_hostile_labels_are_skipped():
    incident = Incident.objects.create()
    _alert(incident, "a", labels={"hostname": "web-1"})
    _alert(incident, "b", labels={"hostname": "web-1"})
    _alert(incident, "c", labels={"hostname": {"nested": "x"}})
    _alert(incident, "d", labels=["not", "a", "dict"])
    _alert(incident, "e")
    empty = Incident.objects.create()

    assert incident_hosts([incident.pk, empty.pk]) == {incident.pk: ("web-1",)}


@pytest.mark.django_db
def test_hosts_cost_one_query(django_assert_num_queries):
    incidents = [Incident.objects.create() for n in range(5)]
    for incident in incidents:
        _alert(incident, f"f{incident.pk}", labels={"hostname": f"h{incident.pk}"})

    with django_assert_num_queries(1):
        incident_hosts([incident.pk for incident in incidents])


@pytest.mark.parametrize(
    ("hosts", "summary"),
    [((), ""), (("web-1",), "web-1"), (("a", "b", "c"), "a +2")],
)
def test_host_summary(hosts, summary):
    assert host_summary(hosts) == summary


def _bare_alert(key, **fields):
    return Alert.objects.create(
        fingerprint=key,
        name=key,
        severity="warning",
        status="firing",
        source="cluster",
        started_at=timezone.now(),
        **fields,
    )


@pytest.mark.django_db
def test_on_node_matches_the_fk_and_unlinked_labels_only():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    other = Node.objects.create(instance_id="i-db", hostname="db")
    linked = _bare_alert("a", node=node)
    by_label = _bare_alert("b", labels={"instance_id": "i-web"})
    by_hostname = _bare_alert("c", labels={"hostname": "web"})
    _bare_alert("d", node=other, labels={"instance_id": "i-web"})
    _bare_alert("e", labels={"instance": "elsewhere"})

    found = set(Alert.objects.filter(on_node(node.pk, ("i-web", "web"))))

    assert found == {linked, by_label, by_hostname}


@pytest.mark.django_db
def test_node_choices_sort_by_name_and_carry_both_keys():
    Node.objects.create(instance_id="i-2", hostname="Zed")
    Node.objects.create(instance_id="i-3", hostname="Abe")
    Node.objects.create(instance_id="i-1", hostname="")

    assert [(name, keys) for _, name, keys in node_choices()] == [
        ("Abe", ("i-3", "Abe")),
        ("Unnamed node", ("i-1",)),
        ("Zed", ("i-2", "Zed")),
    ]


def test_alert_host_prefers_the_node_then_the_labels():
    assert alert_host("web", "i-web", {"instance": "x"}) == "web"
    assert alert_host("", "i-web", {}) == "i-web"
    assert alert_host(None, None, {"instance": "x"}) == "x"
    assert alert_host(None, None, None) == ""
