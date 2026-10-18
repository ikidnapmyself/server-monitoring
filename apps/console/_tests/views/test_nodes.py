import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console._tests.test_templates import FORBIDDEN

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])

pytestmark = pytest.mark.django_db


@pytest.fixture
def url():
    return reverse("console:nodes")


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def _nav(body, label):
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def _open_critical(node):
    incident = Incident.objects.create(severity="critical")
    Alert.objects.create(
        fingerprint=f"fp-{node.pk}",
        name="Disk full",
        severity="critical",
        status="firing",
        source="cluster",
        node=node,
        incident=incident,
        started_at=timezone.now(),
    )
    return incident


def test_the_list_lives_at_nodes(url):
    assert url == "/console/nodes/"


def test_anonymous_is_sent_to_the_admin_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/nodes/"


def test_staff_without_a_node_permission_is_refused(client, bare_staff, url):
    resp = client.get(url)

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to view this page required."


@pytest.mark.parametrize("fixture", ["node_viewer", "node_operator"])
def test_view_or_change_node_reads_the_list(client, request, url, fixture):
    request.getfixturevalue(fixture)

    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/nodes.html"
    assert "<title>Nodes · Console</title>" in body
    assert _nav(body, "Nodes") == f'<a class="c-unav__item" href="{url}" aria-current="page">'
    assert _nav(body, "Inbox") == f'<a class="c-unav__item" href="{reverse("console:inbox")}">'


def test_htmx_request_gets_only_the_partial_rooted_at_the_polled_element(client, node_viewer, url):
    resp = client.get(url, **HTMX)

    body = resp.content.decode()
    assert "<html" not in body
    assert resp.templates[0].name == "console/partials/_nodes.html"
    assert body.lstrip().startswith('<div id="nodes"')
    root = _tag(body, "nodes")
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{url}"' in root
    assert 'hx-trigger="every 30s, console:refresh"' in root
    assert 'hx-swap="outerHTML"' in root


def test_a_row_links_the_node_page_and_its_counts_the_incident_list(client, node_viewer, url):
    node = Node.objects.create(instance_id="i-web", hostname="web")
    _open_critical(node)

    body = client.get(url).content.decode()

    assert f'<a id="node-{node.pk}" href="/console/nodes/{node.pk}/">web</a>' in body
    count = _tag(body, f"node-{node.pk}-critical")
    assert f'href="/console/incidents/?severity=critical&amp;node={node.pk}"' in count
    assert "1 critical</a>" in body
    assert "This hub" not in body and "Peer · seen <time" in body


def test_the_freshness_chips_swap_the_list(client, node_viewer, url):
    Node.objects.create(instance_id="i-web", hostname="web")

    body = client.get(url).content.decode()

    chip = _tag(body, "chip-freshness-silent")
    assert 'href="/console/nodes/?freshness=silent"' in chip
    assert 'hx-target="#nodes"' in chip and 'hx-sync="#nodes:replace"' in chip
    assert 'aria-current="true"' in _tag(body, "chip-freshness-all")


def test_the_search_form_resends_the_freshness(client, node_viewer, url):
    body = client.get(f"{url}?freshness=silent&q=web").content.decode()

    form = body[body.index('<form class="c-search"') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="freshness" value="silent">' in form
    assert 'value="web"' in _tag(form, "nodes-q")
    assert 'placeholder="Hostname or instance ID"' in _tag(form, "nodes-q")


def test_the_header_links_the_fleet_policy(client, node_viewer, url):
    body = client.get(url).content.decode()

    assert 'id="nodes-policy"' in body


@pytest.mark.parametrize(
    ("setup", "query", "heading"),
    [
        (False, "", "No nodes have reported yet."),
        (True, "?q=nothing", "No nodes match these filters."),
    ],
)
def test_each_blank_state(client, node_viewer, url, setup, query, heading):
    if setup:
        Node.objects.create(instance_id="i-web", hostname="web")

    body = client.get(f"{url}{query}").content.decode()

    assert heading in body
    if setup:
        clear = _tag(body, "filters-clear")
        assert f'href="{url}"' in clear and 'hx-target="#nodes"' in clear
    else:
        assert "filters-clear" not in body


def test_an_invalid_filter_is_named(client, node_viewer, url):
    body = client.get(f"{url}?freshness=gone").content.decode()

    assert "Ignored invalid filter: freshness." in body


@BOTH
def test_a_hostile_hostname_is_escaped(client, node_viewer, url, headers):
    Node.objects.create(instance_id="i-bad", hostname=MARKUP)

    body = client.get(url, **headers).content.decode()

    assert ESCAPED in body
    assert "<img" not in body


@BOTH
def test_every_focusable_element_in_the_polled_region_has_a_unique_id(
    client, node_viewer, url, headers
):
    for name in ("web", "db"):
        _open_critical(Node.objects.create(instance_id=f"i-{name}", hostname=name))

    body = client.get(url, **headers).content.decode()
    region = body if headers else body[body.index('<div id="nodes"') : body.index("</main>")]

    tags = re.findall(r"<(?:a|button|input(?![^>]*hidden))\b[^>]*>", region)
    ids = [re.search(r'\sid="([^"]*)"', tag).group(1) for tag in tags]
    assert len(ids) == len(set(ids)) == len(tags)


@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_rendered_list_is_clean(client, node_viewer, url, label, headers):
    _open_critical(Node.objects.create(instance_id="i-web", hostname="web"))

    body = client.get(f"{url}?freshness=bogus", **headers).content.decode()

    assert '<div class="c-flash' in body
    assert not FORBIDDEN[label].search(body), label
