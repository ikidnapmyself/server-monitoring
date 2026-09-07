import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.mark.django_db
def test_dashboard_renders_readiness_and_sections(client):
    get_user_model().objects.create_superuser("admin", "a@b.co", "x")
    client.login(username="admin", password="x")

    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="a", driver="slack", is_active=True)

    resp = client.get(reverse("admin:index"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Readiness" in body
    assert "Notification channels" in body  # a readiness card label
    assert "readiness-card" in body  # status-classed card
    assert "readiness-ok" in body  # channel is active -> ok class
    assert "Operations" in body and "Configuration" in body  # grouped nav grid
    assert reverse("admin:alerts_incident_changelist") in body  # a model link in the grid
    assert reverse("admin:netmap") in body  # readiness heading links to the network map
    assert reverse("admin:policy-overview") in body  # and to the hub-side policy overview


@pytest.mark.django_db
def test_dashboard_renders_the_fleet_metrics_grid(client):
    from django.utils import timezone

    from apps.alerts.models import Alert, Node

    get_user_model().objects.create_superuser("admin2", "a2@b.co", "x")
    client.login(username="admin2", password="x")
    node = Node.objects.create(instance_id="peer-a", hostname="peer-a")
    Alert.objects.create(
        fingerprint="peer-a:memory",
        name="memory",
        severity="warning",
        status="firing",
        source="cluster",
        node=node,
        labels={"checker": "memory", "instance_id": "peer-a"},
        annotations={"memory_percent": "88.0"},
        started_at=timezone.now(),
    )

    body = client.get(reverse("admin:index")).content.decode()
    assert "Fleet metrics" in body
    assert "88.0" in body
    # The colour is on the reading itself, and the unit is in the header.
    assert 'class="metric-value metric-warning"' in body
    assert "metric-unit" in body


@pytest.mark.django_db
def test_dashboard_says_so_when_no_node_reports_a_metric(client):
    get_user_model().objects.create_superuser("admin3", "a3@b.co", "x")
    client.login(username="admin3", password="x")
    body = client.get(reverse("admin:index")).content.decode()
    assert "No node has reported a headline metric yet" in body
