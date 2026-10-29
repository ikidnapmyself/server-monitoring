import json
from unittest import mock

import pytest
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Node
from apps.alerts.reeval_existing import scope_digest
from apps.console.queries.reeval import node_scope
from apps.orchestration.models import PipelineRun

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _url(node, checker=None):
    url = reverse("console:node-reeval", args=[node.pk])
    return f"{url}?checker={checker}" if checker is not None else url


def _digest(node, checker=None):
    return scope_digest(node_scope(node, checker))


@pytest.fixture
def node(reevaluable_alert):
    return reevaluable_alert.node


def _memory_alert(node):
    Node.objects.filter(pk=node.pk).update(
        config={**node.config, "memory": {"warning_threshold": 50, "critical_threshold": 60}}
    )
    node.refresh_from_db()
    return Alert.objects.create(
        fingerprint=f"check:{node.instance_id}:memory",
        source="cluster",
        name="memory high",
        severity="warning",
        status="firing",
        started_at=timezone.now(),
        node=node,
        incident=node.incidents.first(),
        labels={"checker": "memory", "instance_id": node.instance_id},
        annotations={"metrics": json.dumps({"memory_percent": 90.0})},
    )


def test_the_route_lives_under_the_node():
    assert reverse("console:node-reeval", args=[7]) == "/console/nodes/7/reevaluate/"


def test_preview_writes_nothing(client, superuser, node):

    resp = client.get(_url(node))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/reeval.html"
    assert "<title>Re-evaluate the open alerts on web-03 · Console</title>" in body
    assert 'name="digest"' in body and 'id="reeval-apply"' in body
    assert ", reported " in body and " ago." in body
    assert f'href="{reverse("console:node", args=[node.pk])}"' in body
    assert not AlertHistory.objects.exists()


def test_htmx_gets_the_preview_without_the_layout(client, superuser, node):

    resp = client.get(_url(node), **HTMX)

    assert resp.templates[0].name == "console/partials/_reeval.html"
    assert "<title>" not in resp.content.decode()


def test_a_refused_post_lands_on_the_scoped_preview_after_login(client, node):
    resp = client.post(_url(node, "cpu"), {"digest": "x"})

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next={_url(node)}%3Fchecker%3Dcpu"


def test_apply_writes_history_enqueues_and_redirects(client, superuser, node):
    alert = node.alerts.get()

    resp = client.post(_url(node), {"digest": _digest(node)})

    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:node", args=[node.pk])
    assert AlertHistory.objects.filter(alert=alert).exists()
    assert PipelineRun.objects.filter(incident=alert.incident).exists()
    assert "Resolved 0, changed severity on 1" in client.get(resp["Location"]).content.decode()


def test_a_checker_scope_applies_only_that_checker(client, superuser, node):
    memory = _memory_alert(node)

    body = client.get(_url(node, "cpu")).content.decode()
    assert f'action="{_url(node, "cpu")}"' in body
    resp = client.post(_url(node, "cpu"), {"digest": _digest(node, "cpu")})

    assert resp.status_code == 303
    memory.refresh_from_db()
    assert memory.severity == "warning"
    assert not AlertHistory.objects.filter(alert=memory).exists()
    assert node.alerts.get(labels__checker="cpu").severity == "warning"


def test_a_stale_digest_refuses_with_a_fresh_preview(client, superuser, node):
    resp = client.post(_url(node), {"digest": "stale"})

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "This node&#x27;s alerts or policy changed since you looked." in body
    assert 'id="reeval-apply"' in body
    assert not AlertHistory.objects.exists()


def test_a_double_submit_applies_once(client, superuser, node):
    digest = _digest(node)

    assert client.post(_url(node), {"digest": digest}).status_code == 303
    assert client.post(_url(node), {"digest": digest}).status_code == 409
    assert AlertHistory.objects.count() == 1


def test_a_busy_database_is_a_409(client, superuser, node):
    with mock.patch(
        "apps.console.views.node_reeval.apply_reeval",
        side_effect=OperationalError("database is locked"),
    ):
        resp = client.post(_url(node), {"digest": _digest(node)})

    assert resp.status_code == 409
    assert not AlertHistory.objects.exists()


def test_an_empty_scope_offers_no_apply(client, superuser, node):
    Alert.objects.update(status="resolved")

    body = client.get(_url(node)).content.decode()

    assert "No open alerts need re-evaluation." in body
    assert 'id="reeval-apply"' not in body


def test_a_crafted_post_on_an_empty_scope_applies_nothing(client, superuser, node):
    Alert.objects.update(status="resolved")

    resp = client.post(_url(node), {"digest": _digest(node)})

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "No open alerts need re-evaluation." in body
    assert "Resolved 0" not in body
    assert not AlertHistory.objects.exists()
    assert not PipelineRun.objects.exists()
    assert "Resolved 0" not in client.get(reverse("console:node", args=[node.pk])).content.decode()


def test_an_empty_checker_means_the_whole_node(client, superuser, node):
    body = client.get(_url(node, "")).content.decode()

    assert "Re-evaluate the open alerts on web-03" in body
    assert 'id="reeval-apply"' in body


@pytest.mark.parametrize("checker", ["%20", "%00", "%20%00%20"])
def test_a_checker_that_cleans_to_blank_narrows_to_nothing(client, superuser, node, checker):
    body = client.get(_url(node, checker)).content.decode()

    assert "Re-evaluate one checker&#x27;s alerts on web-03" in body
    assert 'id="reeval-apply"' not in body

    resp = client.post(_url(node, checker), {"digest": _digest(node)})

    assert resp.status_code == 409
    assert not AlertHistory.objects.exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_node_is_the_console_404_page(client, superuser, method):
    resp = getattr(client, method)(reverse("console:node-reeval", args=[999]))

    assert resp.status_code == 404
    assert "This node doesn't exist." in resp.content.decode()
