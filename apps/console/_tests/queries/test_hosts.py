import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.queries.hosts import host_summary, incident_hosts


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
