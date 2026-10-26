import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.describing import describe_alert
from apps.alerts.models import Alert, Node
from apps.alerts.node_policy import config_digest
from apps.console.queries.allow_ports import parse_ports

pytestmark = pytest.mark.django_db

METRICS = {
    "unexpected_ports": [22, 80],
    "listening": [{"port": 22}, {"port": 80}, {"port": 5432}],
}


@pytest.fixture
def node():
    return Node.objects.create(instance_id="web-1", hostname="web-1", config={})


@pytest.fixture
def alert(node):
    return Alert.objects.create(
        fingerprint="check:web-1:listening_ports",
        name="listening_ports",
        severity="warning",
        status="firing",
        source="cluster",
        node=node,
        labels={"checker": "listening_ports", "instance_id": "web-1"},
        raw_payload={"metrics": METRICS},
        started_at=timezone.now(),
    )


def _url(node):
    return reverse("console:node-allow-ports", args=[node.pk])


def test_ports_parse_to_a_sorted_valid_set():
    assert parse_ports("80, 22,x,0,70000,22") == (22, 80)


def test_the_alert_offers_to_allow_every_port(node, alert):
    action = describe_alert(alert).action

    assert action.intent == "allow_node_ports"
    assert action.text == "Allow 22, 80 on this node"
    assert action.params == {"pk": node.pk, "ports": [22, 80]}


def test_a_resolved_or_unlinked_alert_keeps_the_checker_text(node, alert):
    Alert.objects.filter(pk=alert.pk).update(status="resolved")
    alert.refresh_from_db()
    assert describe_alert(alert).action is None or describe_alert(alert).action.intent == ""

    Alert.objects.filter(pk=alert.pk).update(status="firing", node=None)
    alert.refresh_from_db()
    assert describe_alert(alert).action.intent == ""


def test_confirm_page_shows_the_result_and_seeded_ports(client, node_operator, node, alert):
    body = client.get(_url(node), {"ports": "22,80"}).content.decode()

    assert 'id="allow-ports"' in body
    assert "<code>5432</code>" in body
    assert 'id="allow-seeded"' in body
    assert 'id="allow-apply"' in body


def test_a_viewer_sees_no_apply_button(client, node_viewer, node, alert):
    body = client.get(_url(node), {"ports": "22"}).content.decode()

    assert 'id="allow-apply"' not in body


def test_no_ports_has_nothing_to_apply(client, node_operator, node):
    body = client.get(_url(node)).content.decode()

    assert "No ports to allow." in body
    assert 'id="allow-apply"' not in body


def test_an_unknown_node_is_a_404(client, node_operator):
    assert client.get(reverse("console:node-allow-ports", args=[999])).status_code == 404


def test_applying_saves_and_sends_the_operator_to_the_preview(client, node_operator, node, alert):
    response = client.post(_url(node), {"ports": "22,80", "digest": config_digest(node.config)})

    node.refresh_from_db()
    assert node.config["listening_ports"]["allowlist"] == [22, 80, 5432]
    assert response.status_code == 303
    assert response["Location"].startswith(reverse("console:node-reeval", args=[node.pk]))


def test_applying_without_a_change_in_scoring_goes_back_to_the_node(client, node_operator, node):
    node.config = {"listening_ports": {"allowlist": [22, 80]}}
    node.save()

    response = client.post(_url(node), {"ports": "22", "digest": config_digest(node.config)})

    assert response["Location"] == reverse("console:node", args=[node.pk])


def test_a_stale_digest_is_a_409(client, node_operator, node):
    response = client.post(_url(node), {"ports": "22", "digest": "old"})

    assert response.status_code == 409
    node.refresh_from_db()
    assert node.config == {}


def test_applying_nothing_is_a_409(client, node_operator, node):
    response = client.post(_url(node), {"ports": "", "digest": config_digest(node.config)})

    assert response.status_code == 409


def test_a_viewer_cannot_apply(client, node_viewer, node):
    response = client.post(_url(node), {"ports": "22", "digest": config_digest(node.config)})

    assert response.status_code == 403


def test_applying_to_an_unknown_node_is_a_404(client, node_operator):
    response = client.post(reverse("console:node-allow-ports", args=[999]), {"ports": "22"})

    assert response.status_code == 404


def test_a_lost_race_on_the_database_is_a_409(client, node_operator, node, monkeypatch):
    from django.db import OperationalError

    def locked(*args, **kwargs):
        raise OperationalError("database is locked")

    monkeypatch.setattr("apps.console.views.node_allow_ports.save_policy", locked)

    response = client.post(_url(node), {"ports": "22", "digest": config_digest(node.config)})

    assert response.status_code == 409
