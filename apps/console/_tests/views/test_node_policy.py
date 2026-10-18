import re
from unittest import mock

import pytest
from django.db import OperationalError
from django.urls import reverse

from apps.alerts.models import Node
from apps.alerts.node_policy import config_digest
from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import focus_audit

pytestmark = pytest.mark.django_db

CPU = {"cpu": {"warning_threshold": 80, "critical_threshold": 90}}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"


@pytest.fixture
def node():
    return Node.objects.create(instance_id="web-1", hostname="web-1", config=dict(CPU))


def _url(node):
    return reverse("console:node-policy", args=[node.pk])


def _boxes(warning=80, critical=90, **extra):
    return {
        "policy__cpu__warning_threshold": warning,
        "policy__cpu__critical_threshold": critical,
        **extra,
    }


def _post(client, node, data):
    return client.post(_url(node), {"digest": config_digest(node.config), **data})


def test_the_editor_lives_under_the_node():
    assert reverse("console:node-policy", args=[7]) == "/console/nodes/7/policy/"
    assert reverse("console:node-reeval", args=[7]) == "/console/nodes/7/reevaluate/"


def test_a_stale_save_is_a_409_with_the_fresh_boxes(client, node_operator, node):
    digest = config_digest(node.config)
    Node.objects.filter(pk=node.pk).update(
        config={"cpu": {"warning_threshold": 60, "critical_threshold": 95}}
    )

    response = client.post(_url(node), {"digest": digest, **_boxes(warning=70)})

    body = response.content.decode()
    assert response.status_code == 409
    assert "This node&#x27;s policy changed since you opened it." in body
    assert 'value="60"' in body
    node.refresh_from_db()
    assert node.config["cpu"]["warning_threshold"] == 60


def test_a_busy_database_is_a_409_with_the_fresh_boxes(client, node_operator, node):
    with mock.patch(
        "apps.console.views.node_policy.save_policy",
        side_effect=OperationalError("database is locked"),
    ):
        response = _post(client, node, _boxes(warning=70))

    assert response.status_code == 409
    assert "This node&#x27;s policy changed since you opened it." in response.content.decode()
    node.refresh_from_db()
    assert node.config == CPU


def test_anonymous_is_sent_to_the_admin_login(client, node):
    resp = client.get(_url(node))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(node)}"


def test_an_anonymous_save_logs_in_back_to_the_editor(client, node):
    resp = client.post(_url(node), _boxes())

    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(node)}"


def test_a_viewer_is_refused_on_get_and_on_post(client, node_viewer, node):
    assert client.get(_url(node)).status_code == 403
    resp = _post(client, node, _boxes(warning=70))

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to change nodes required."
    node.refresh_from_db()
    assert node.config == CPU


def test_the_editor_renders_the_boxes_and_the_digest(client, node_operator, node):
    resp = client.get(_url(node))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/node_policy.html"
    assert "<title>Policy · web-1 · Console</title>" in body
    assert '<fieldset id="policy-cpu"' in body
    assert 'name="policy__cpu__warning_threshold"' in body
    assert f'name="digest" value="{config_digest(node.config)}"' in body
    assert 'id="policy-add"' in body
    assert f'<a id="policy-back" class="c-crumb" href="/console/nodes/{node.pk}/">' in body
    assert "data-poll" not in body[body.index("<main") :]


def test_the_help_text_is_what_the_box_points_at(client, node_operator, node):
    body = client.get(_url(node)).content.decode()

    box = re.search(r'<input[^>]*name="policy__cpu__warning_threshold"[^>]*>', body).group()
    assert 'aria-describedby="id_policy__cpu__warning_threshold_helptext"' in box
    assert '<p id="id_policy__cpu__warning_threshold_helptext" class="c-field__help">' in body


def test_htmx_gets_the_editor_without_the_layout(client, node_operator, node):
    resp = client.get(_url(node), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_node_policy.html"
    assert "<html" not in resp.content.decode()


def test_a_node_with_nothing_to_edit_says_so(client, node_operator):
    bare = Node.objects.create(instance_id="db-1", hostname="db-1")

    body = client.get(_url(bare)).content.decode()

    assert "This node reports no checker that takes a policy yet." in body


def test_a_scoring_change_lands_on_the_preview_and_applies_nothing(client, node_operator, node):
    resp = _post(client, node, _boxes(warning=70))

    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:node-reeval", args=[node.pk])
    node.refresh_from_db()
    assert node.config["cpu"]["warning_threshold"] == 70
    body = client.get(resp["Location"]).content.decode()
    assert "This policy doesn&#x27;t touch the alerts already open." in body


def test_an_untouched_save_lands_on_the_node_page(client, node_operator, node):
    resp = _post(client, node, _boxes())

    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:node", args=[node.pk])
    assert "Policy saved." in client.get(resp["Location"]).content.decode()


def test_a_save_does_not_bump_last_seen(client, node_operator, node):
    before = Node.objects.get(pk=node.pk).last_seen

    _post(client, node, _boxes(warning=70))

    assert Node.objects.get(pk=node.pk).last_seen == before


def test_opening_a_section_lands_back_on_its_boxes(client, node_operator, node):
    resp = _post(client, node, _boxes(policy__add_section="memory"))

    assert resp.status_code == 303
    assert resp["Location"] == f"{_url(node)}#policy-memory"
    node.refresh_from_db()
    assert node.config["memory"] == {}
    body = client.get(resp["Location"]).content.decode()
    assert "Opened the" in body and '<fieldset id="policy-memory"' in body


def test_an_invalid_box_writes_nothing_and_keeps_the_posted_digest(client, node_operator, node):
    resp = client.post(_url(node), {"digest": "posted", "policy__cpu__warning_threshold": 80})

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "Set a critical threshold too, or clear both." in body
    assert 'name="digest" value="posted"' in body
    node.refresh_from_db()
    assert node.config == CPU


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_node_is_the_console_404_page(client, node_operator, method):
    resp = getattr(client, method)(reverse("console:node-policy", args=[999]))

    assert resp.status_code == 404
    assert "This node doesn't exist." in resp.content.decode()


def test_an_unnamed_node_hides_its_id(client, node_operator):
    uuid = "0b9e6c1e-6f4a-4c43-9a59-5b3f1f2a7c11"
    bare = Node.objects.create(instance_id=uuid, hostname="", config=dict(CPU))

    body = client.get(_url(bare)).content.decode()

    assert "Policy for Unnamed node" in body
    assert uuid not in visible(body)


def test_a_hostile_hostname_is_escaped(client, node_operator):
    bad = Node.objects.create(instance_id="bad", hostname=MARKUP, config=dict(CPU))

    body = client.get(_url(bad)).content.decode()

    assert ESCAPED in body
    assert "<img" not in body


@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_the_rendered_editor_is_clean(client, node_operator, node, label):
    body = client.get(_url(node)).content.decode()

    assert not FORBIDDEN[label].search(body), label


def test_every_focusable_element_has_a_stable_id(client, node_operator, node):
    body = client.get(_url(node)).content.decode()
    region = body[body.index("<main") : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))
