import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Node
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import focus_audit

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"


@pytest.fixture
def url():
    return reverse("console:policy")


@pytest.fixture
def node():
    node = Node.objects.create(
        instance_id="i-web",
        hostname="web",
        config={"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
    )
    Alert.objects.create(
        fingerprint="check:i-web:cpu",
        source="cluster",
        name="cpu",
        severity="critical",
        status="firing",
        started_at=timezone.now(),
        node=node,
        labels={"checker": "cpu", "instance_id": "i-web"},
    )
    return node


def _nav(body, label):
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def test_the_page_lives_at_policy(url):
    assert url == "/console/policy/"


def test_anonymous_is_sent_to_the_console_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next=/console/policy/"


def test_a_superuser_reads_the_page_with_nodes_current(client, superuser, url, node):
    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/policy.html"
    assert "<title>Fleet policy · Console</title>" in body
    assert "<h1" in body and "Fleet policy</h1>" in body
    assert 'aria-current="page"' in _nav(body, "Nodes")
    assert f'id="policy-node-{node.pk}"' in body
    assert f'<a id="policy-node-{node.pk}-open" href="/console/nodes/{node.pk}/">' in body
    assert "data-poll" not in body


def test_htmx_gets_the_partial(client, superuser, url, node):
    resp = client.get(url, **HTMX)

    assert resp.templates[0].name == "console/partials/_policy.html"
    assert "<html" not in resp.content.decode()


def test_a_superuser_gets_both_links(client, superuser, url, node):
    body = client.get(url).content.decode()

    assert (
        f'<a id="policy-{node.pk}-cpu-edit" href="/console/nodes/{node.pk}/policy/#policy-cpu">'
        in body
    )
    assert (
        f'<a id="policy-{node.pk}-cpu-reeval" '
        f'href="/console/nodes/{node.pk}/reevaluate/?checker=cpu">' in body
    )


def test_unread_keys_and_quiet_nodes(client, superuser, url):
    Node.objects.create(
        instance_id="i-db", hostname="db", config={"listening_ports": {"extra_key": 1}}
    )
    Node.objects.create(instance_id="i-quiet", hostname="quiet")

    body = client.get(url).content.decode()

    assert "listening_ports → extra_key" in body
    assert "1 node holds no policy and fires nothing." in body


def test_no_policy_anywhere_says_so(client, superuser, url):
    body = client.get(url).content.decode()

    assert "No node holds a policy yet." in body
    assert "fires nothing" not in body


def test_a_hostile_hostname_is_escaped(client, superuser, url):
    Node.objects.create(
        instance_id="i-bad", hostname=MARKUP, config={"cpu": {"warning_threshold": 80}}
    )

    body = client.get(url).content.decode()

    assert ESCAPED in body and "<img" not in body


def test_every_focusable_element_has_a_stable_id(client, superuser, url, node):
    body = client.get(url).content.decode()
    region = body[body.index("<main") : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_the_rendered_page_is_clean(client, superuser, url, node, label):
    assert not FORBIDDEN[label].search(client.get(url).content.decode()), label
