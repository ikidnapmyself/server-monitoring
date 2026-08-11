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
