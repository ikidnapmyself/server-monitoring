"""The /admin/policy/ page.

Mirrors config/_tests/test_netmap.py: the projection itself is tested in
apps/alerts/_tests/test_policy_overview.py, so this covers reaching the page and
what the template does with what it is handed.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.alerts.models import Node

pytestmark = pytest.mark.django_db


def test_anonymous_is_redirected_to_login(client):
    assert client.get(reverse("admin:policy-overview")).status_code == 302


def test_staff_without_view_node_is_refused(client):
    user = get_user_model().objects.create_user(username="plain", password="pw", is_staff=True)
    client.force_login(user)
    assert client.get(reverse("admin:policy-overview")).status_code == 403


def test_staff_with_view_node_gets_the_page(client):
    user = get_user_model().objects.create_user(username="viewer", password="pw", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_node"))
    client.force_login(user)
    response = client.get(reverse("admin:policy-overview"))
    assert response.status_code == 200
    assert "Hub-side policy" in response.content.decode()


def test_a_configured_node_renders_its_row_and_an_edit_link(admin_client):
    node = Node.objects.create(
        instance_id="fiyat-ekrani",
        config={"cpu": {"warning_threshold": 90, "critical_threshold": 99}},
    )
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "fiyat-ekrani" in body
    assert "Warning at 90, Critical at 99" in body
    assert f"/admin/alerts/node/{node.pk}/change/#id_policy__cpu__warning_threshold" in body


def test_a_broken_policy_shows_its_reason(admin_client):
    Node.objects.create(instance_id="a", config={"cpu": {"warning_threshold": 90}})
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "Saved but not scoring" in body
    assert "Set a critical threshold too, or clear both." in body


def test_nodes_with_no_policy_are_counted(admin_client):
    Node.objects.create(instance_id="a", config={"cpu": {"warning_threshold": 1}})
    Node.objects.create(instance_id="b", config={})
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "1 other node has no hub-side policy" in body


def test_an_unconfigured_hub_says_so(admin_client):
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "No node on this hub overrides anything" in body


def test_a_hostname_is_escaped(admin_client):
    # instance_id and hostname both arrive over a webhook.
    Node.objects.create(
        instance_id="<script>x</script>",
        hostname="<b>y</b>",
        config={"cpu": {"warning_threshold": 1}},
    )
    body = admin_client.get(reverse("admin:policy-overview")).content.decode()
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body
