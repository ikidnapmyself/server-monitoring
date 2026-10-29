import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, Incident, Node
from apps.checkers.models import CheckRun, PreflightRun
from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import focus_audit
from apps.orchestration.models import PipelineRun

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
UUID = "0b9e6c1e-6f4a-4c43-9a59-5b3f1f2a7c11"


def _url(node):
    return reverse("console:node", args=[node.pk])


def _nav(body, label):
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def _busy(**fields):
    node = Node.objects.create(
        **{
            "instance_id": "i-web",
            "hostname": "web",
            "config": {"cpu": {"warning_threshold": 80, "critical_threshold": 90, "x_y": 1}},
            **fields,
        }
    )
    incident = Incident.objects.create(severity="critical", status="open")
    Alert.objects.create(
        fingerprint="check:i-web:cpu",
        name="cpu",
        severity="critical",
        status="firing",
        source="cluster",
        node=node,
        incident=incident,
        labels={"checker": "cpu"},
        started_at=timezone.now(),
    )
    return node, incident


def _hub():
    node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
    for value in (10.0, 20.0, 30.0):
        CheckRun.objects.create(
            checker_name="cpu", hostname="hub", status="ok", metrics={"cpu_percent": value}
        )
    return node


def test_the_page_lives_under_nodes():
    assert reverse("console:node", args=[7]) == "/console/nodes/7/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_console_login(client):
    node, _ = _busy()

    resp = client.get(_url(node))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next={_url(node)}"


@pytest.mark.django_db
def test_a_superuser_reads_the_page_with_the_nav_tab_current(client, superuser):
    node, _ = _busy()

    resp = client.get(_url(node))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/node.html"
    assert "<title>web · Console</title>" in body
    assert 'aria-current="page"' in _nav(body, "Nodes")
    assert 'id="node-back"' in body and "Back to nodes" in body
    assert re.search(rf'<div id="node" data-poll hx-get="{_url(node)}"', body)


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, superuser):
    node, _ = _busy()

    resp = client.get(_url(node), **HTMX)

    assert resp.templates[0].name == "console/partials/_node.html"
    assert "<html" not in resp.content.decode()


@pytest.mark.django_db
@BOTH
def test_a_missing_node_is_the_console_404_page(client, superuser, headers):
    resp = client.get(reverse("console:node", args=[999]), **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This node doesn't exist." in body
    assert f'<a id="not-found-back" href="{reverse("console:nodes")}">Back to nodes</a>' in body


@pytest.mark.django_db
def test_the_page_links_the_incident_the_counts_and_the_runs(client, superuser):
    node, incident = _busy()
    run = PipelineRun.objects.create(run_id="r-1", trace_id="t-1", node=node, incident=incident)

    body = client.get(_url(node)).content.decode()

    assert f'id="node-incident-{incident.pk}" href="/console/incidents/{incident.pk}/"' in body
    count = re.search(r'<a id="node-count-critical"[^>]*>', body).group()
    assert f'href="/console/incidents/?severity=critical&amp;node={node.pk}"' in count
    assert f'<a id="node-runs" href="/console/runs/?node={node.pk}">' in body
    assert f'<a id="run-{run.pk}" href="/console/runs/{run.pk}/">' in body
    assert f'<a id="run-{run.pk}-incident" href="/console/incidents/{incident.pk}/">' in body
    assert re.search(r'<a id="checker-cpu" href="/console/alerts/\d+/">', body)
    assert "Warning at 80" in body
    assert "1 stored setting no scorer reads." in body


@pytest.mark.django_db
def test_the_hub_checker_row_opens_its_check_run_in_the_console(client, superuser):
    node = _hub()
    newest = CheckRun.objects.order_by("-executed_at", "-pk").first()

    body = client.get(_url(node)).content.decode()

    assert re.search(rf'<a id="checker-cpu" href="/console/checks/{newest.pk}/">', body)


@pytest.mark.django_db
def test_the_hub_preflight_opens_in_the_console(client, superuser):
    node = _hub()
    run = PreflightRun.objects.create(instance_id=local_instance_id(), passed=4)

    body = client.get(_url(node)).content.decode()

    assert f'<a id="node-preflight" href="/console/preflight/{run.pk}/">' in body


@pytest.mark.django_db
def test_a_superuser_gets_the_policy_links(client, superuser):
    node, _ = _busy()

    body = client.get(_url(node)).content.decode()

    assert f'<a id="policy-edit-cpu" href="/console/nodes/{node.pk}/policy/#policy-cpu">' in body
    assert (
        f'id="action-edit_node_policy" class="c-button c-button--sm c-button--primary" href="/console/nodes/{node.pk}/policy/"'
        in body
    )


@pytest.mark.django_db
def test_a_superuser_sees_both_actions(client, superuser):
    node, _ = _busy()

    body = client.get(_url(node)).content.decode()

    assert 'id="action-edit_node_policy"' in body
    assert re.search(
        rf'id="action-reevaluate_node"[^>]* href="/console/nodes/{node.pk}/reevaluate/"', body
    )


@pytest.mark.django_db
def test_a_node_with_no_policy_says_so(client, superuser):
    node = _busy()[0]
    Node.objects.filter(pk=node.pk).update(config={})
    Incident.objects.update(status="resolved")

    body = client.get(_url(node)).content.decode()

    assert "No policy set on this node." in body
    assert "Nothing open on this node." in body


@pytest.mark.django_db
def test_a_uuid_instance_id_stays_in_details(client, superuser):
    node = Node.objects.create(instance_id=UUID, hostname="")

    body = client.get(_url(node)).content.decode()

    assert UUID in body
    assert UUID not in visible(body)
    assert "Unnamed node" in visible(body)


@pytest.mark.django_db
@BOTH
def test_hostile_text_is_escaped(client, superuser, headers):
    node, _ = _busy(hostname=MARKUP, labels={"role": MARKUP})

    body = client.get(_url(node), **headers).content.decode()

    assert ESCAPED in body
    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_page_has_no_inline_script_or_style(client, superuser, label, headers):
    body = client.get(_url(_hub()), **headers).content.decode()

    assert "<svg" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_stable_id(client, superuser, headers):
    node, _ = _busy()

    body = client.get(_url(node), **headers).content.decode()
    region = body if headers else body[body.index('<div id="node"') : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))
