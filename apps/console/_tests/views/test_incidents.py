import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries import incidents as incident_queries

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"


@pytest.fixture
def url():
    return reverse("console:incidents")


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    client.force_login(user)
    return user


def _alert(incident, node, key="a"):
    return Alert.objects.create(
        fingerprint=f"{incident.pk}:{key}",
        name=key,
        severity="warning",
        status="firing",
        source="cluster",
        node=node,
        incident=incident,
        started_at=timezone.now(),
    )


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def test_the_list_lives_at_incidents(url):
    assert url == "/console/incidents/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client, url):
    resp = client.get(f"{url}?status=all")

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/incidents/%3Fstatus%3Dall"


@pytest.mark.django_db
def test_anonymous_htmx_request_is_told_to_do_a_full_redirect(client, url):
    resp = client.get(url, **HTMX)

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next=/console/incidents/"


@pytest.mark.django_db
def test_non_staff_is_refused(client, url):
    client.force_login(get_user_model().objects.create_user("visitor", password="x"))

    resp = client.get(url)

    assert resp.status_code == 403
    assert "Staff access required." in resp.content.decode()


@pytest.mark.django_db
def test_staff_gets_the_full_page_with_the_sidebar_entry_current(client, staff, url):
    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "<html" in body
    assert resp.templates[0].name == "console/incidents.html"
    assert "<title>Incidents · Console</title>" in body
    assert f'<a href="{url}" aria-current="page">Incidents</a>' in body
    assert f'<a href="{reverse("console:overview")}">Overview</a>' in body
    assert "script-src 'self'" in resp["Content-Security-Policy"]


@pytest.mark.django_db
def test_the_overview_sidebar_links_to_the_list_without_marking_it_current(client, staff, url):
    body = client.get(reverse("console:overview")).content.decode()

    assert f'<a href="{url}">Incidents</a>' in body
    assert f'<a href="{reverse("console:overview")}" aria-current="page">Overview</a>' in body


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial_rooted_at_the_polled_element(client, staff, url):
    resp = client.get(url, **HTMX)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "<html" not in body
    assert [t.name for t in resp.templates] == ["console/partials/_incidents.html"]
    assert body.lstrip().startswith('<div id="incidents"')
    assert body.rstrip().endswith("</div>")
    assert body.count('id="incidents"') == 1
    assert "HX-Request" in resp["Vary"]


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["HTTP_HX_HISTORY_RESTORE_REQUEST", "HTTP_HX_BOOSTED"])
def test_htmx_request_that_swaps_the_body_gets_the_full_page(client, staff, url, header):
    resp = client.get(url, **HTMX, **{header: "true"})

    assert "<html" in resp.content.decode()
    assert resp.templates[0].name == "console/incidents.html"


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_the_polled_element_requests_the_current_filters(client, staff, url, headers):
    body = client.get(f"{url}?severity=critical&status=all&q=x", **headers).content.decode()

    root = _tag(body, "incidents")
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{url}?status=all&amp;severity=critical"' in root
    assert 'hx-trigger="every 30s, console:refresh"' in root
    assert 'hx-swap="outerHTML"' in root


@pytest.mark.django_db
def test_rows_show_badges_title_nodes_count_and_age(client, staff, url):
    incident = Incident.objects.create(title="Disk full on web-1", severity="critical")
    for index, name in enumerate(["web-1", "web-2"]):
        _alert(incident, Node.objects.create(instance_id=name, hostname=name), key=str(index))
    lonely = Incident.objects.create(title="Swap low", severity="warning", status="acknowledged")

    body = client.get(url).content.decode()

    admin_url = reverse("admin:alerts_incident_change", args=[incident.pk])
    assert f'<a id="incident-{incident.pk}" href="{admin_url}">Disk full on web-1</a>' in body
    assert '<span class="c-badge c-tone-critical">critical</span>' in body
    assert '<span class="c-badge c-tone-critical">open</span>' in body
    assert '<span class="c-badge c-tone-warning">acknowledged</span>' in body
    assert "web-1 +1 · 2 alerts" in body
    assert re.search(r'<span class="c-muted">\s*0 alerts\s*</span>', body)
    assert f'id="incident-{lonely.pk}"' in body
    assert re.search(r'<time class="c-muted" datetime="[^"]+" title="[^"]+">[^<]+ ago</time>', body)
    assert "2 incidents" in body
    assert "No incidents" not in body


@pytest.mark.django_db
def test_a_single_alert_and_a_single_incident_read_in_the_singular(client, staff, url):
    incident = Incident.objects.create(title="one", severity="info")
    _alert(incident, None)

    body = client.get(url).content.decode()

    assert re.search(r'<span class="c-muted">\s*1 alert\s*</span>', body)
    assert re.search(r">\s*1 incident\s*<", body)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", {"open crit", "acked warn"}),
        ("?status=resolved", {"resolved info"}),
        ("?status=all", {"open crit", "acked warn", "resolved info"}),
        ("?severity=warning", {"acked warn"}),
        ("?status=all&since=24h", {"open crit", "acked warn", "resolved info"}),
    ],
)
def test_each_filter_narrows_the_rows(client, staff, url, query, expected):
    Incident.objects.create(title="open crit", severity="critical")
    Incident.objects.create(title="acked warn", severity="warning", status="acknowledged")
    Incident.objects.create(title="resolved info", severity="info", status="resolved")

    body = client.get(f"{url}{query}").content.decode()

    shown = {title for title in ("open crit", "acked warn", "resolved info") if title in body}
    assert shown == expected


@pytest.mark.django_db
def test_the_node_filter_narrows_the_rows(client, staff, url):
    node = Node.objects.create(instance_id="web-1", hostname="web-1")
    _alert(Incident.objects.create(title="on the node", severity="critical"), node)
    Incident.objects.create(title="elsewhere", severity="critical")

    body = client.get(f"{url}?node={node.pk}").content.decode()

    assert "on the node" in body
    assert "elsewhere" not in body
    assert 'aria-current="true"' in _tag(body, f"chip-node-{node.pk}")
    assert 'aria-current="true"' not in _tag(body, "chip-node-any")


@pytest.mark.django_db
def test_the_since_filter_narrows_the_rows(client, staff, url):
    old = Incident.objects.create(title="last month", severity="critical")
    Incident.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=20))
    Incident.objects.create(title="today", severity="critical")

    body = client.get(f"{url}?since=7d").content.decode()

    assert "today" in body
    assert "last month" not in body


@pytest.mark.django_db
def test_chips_are_links_that_also_swap_the_list_in_place(client, staff, url):
    node = Node.objects.create(instance_id="web-1", hostname="web-1")

    body = client.get(f"{url}?severity=critical").content.decode()

    chip = _tag(body, "chip-status-resolved")
    target = f"{url}?status=resolved&amp;severity=critical"
    assert chip.startswith("<a ")
    assert f'href="{target}"' in chip
    assert f'hx-get="{target}"' in chip
    assert 'hx-target="#incidents"' in chip
    assert 'hx-swap="outerHTML"' in chip
    assert 'hx-push-url="true"' in chip
    assert 'aria-current="true"' not in chip
    assert 'aria-current="true"' in _tag(body, "chip-status-active")
    assert 'aria-current="true"' in _tag(body, "chip-severity-critical")
    assert 'aria-current="true"' in _tag(body, "chip-since-any")
    assert f'href="{url}?severity=critical&amp;node={node.pk}"' in _tag(
        body, f"chip-node-{node.pk}"
    )
    assert body.count('aria-current="true"') == 4
    for label in ("Status", "Severity", "Node", "Since"):
        assert f'aria-label="{label}"' in body


@pytest.mark.django_db
def test_the_node_chips_are_left_out_when_no_node_exists(client, staff, url):
    body = client.get(url).content.decode()

    assert "chip-node-" not in body
    assert 'aria-label="Node"' not in body
    assert body.count('aria-current="true"') == 3


@pytest.mark.django_db
def test_invalid_filters_are_named_in_a_notice(client, staff, url):
    Incident.objects.create(title="still listed", severity="critical")

    body = client.get(f"{url}?status=bogus&node=999&page=x").content.decode()

    assert re.search(
        r'<p class="c-notice c-tone-warning" role="status">\s*'
        r"Ignored invalid filter: status, page, node\.\s*</p>",
        body,
    )
    assert "still listed" in body
    assert f'hx-get="{url}"' in _tag(body, "incidents")


@pytest.mark.django_db
def test_valid_filters_show_no_notice(client, staff, url):
    body = client.get(f"{url}?status=all").content.decode()

    assert "c-notice" not in body
    assert "Ignored invalid filter" not in body


@pytest.mark.django_db
def test_an_install_with_no_incidents_says_so(client, staff, url):
    body = client.get(f"{url}?severity=critical").content.decode()

    assert "No incidents yet." in body
    assert "No incidents match these filters." not in body
    assert "filters-clear" not in body
    assert "page-prev" not in body and "page-next" not in body


@pytest.mark.django_db
def test_filters_that_match_nothing_offer_a_way_to_clear_them(client, staff, url):
    Incident.objects.create(title="open crit", severity="critical")

    body = client.get(f"{url}?severity=info").content.decode()

    assert "No incidents match these filters." in body
    assert "No incidents yet." not in body
    clear = _tag(body, "filters-clear")
    assert f'href="{url}"' in clear
    assert f'hx-get="{url}"' in clear
    assert 'hx-target="#incidents"' in clear
    assert 'hx-swap="outerHTML"' in clear
    assert 'hx-push-url="true"' in clear


@pytest.mark.django_db
def test_default_filters_that_match_nothing_have_nothing_to_clear(client, staff, url):
    Incident.objects.create(title="done", severity="critical", status="resolved")

    body = client.get(url).content.decode()

    assert "No incidents match these filters." in body
    assert "filters-clear" not in body


@pytest.mark.django_db
def test_pager_links_keep_the_filters(client, staff, url, monkeypatch):
    monkeypatch.setattr(incident_queries, "PAGE_SIZE", 1)
    for index in range(3):
        Incident.objects.create(title=f"incident {index}", severity="critical")

    body = client.get(f"{url}?severity=critical&page=2").content.decode()

    prev_link, next_link = _tag(body, "page-prev"), _tag(body, "page-next")
    assert f'href="{url}?severity=critical"' in prev_link
    assert f'hx-get="{url}?severity=critical"' in prev_link
    assert f'href="{url}?severity=critical&amp;page=3"' in next_link
    assert f'hx-get="{url}?severity=critical&amp;page=3"' in next_link
    for link in (prev_link, next_link):
        assert link.startswith("<a ")
        assert 'hx-target="#incidents"' in link
        assert 'hx-swap="outerHTML"' in link
        assert 'hx-push-url="true"' in link
    assert "Page 2 of 3" in body
    assert f'hx-get="{url}?severity=critical&amp;page=2"' in _tag(body, "incidents")


@pytest.mark.django_db
def test_the_first_and_last_pages_link_one_way_only(client, staff, url, monkeypatch):
    monkeypatch.setattr(incident_queries, "PAGE_SIZE", 1)
    for index in range(2):
        Incident.objects.create(title=f"incident {index}", severity="critical")

    first = client.get(url).content.decode()
    last = client.get(f"{url}?page=2").content.decode()

    assert "page-next" in first and "page-prev" not in first
    assert "page-prev" in last and "page-next" not in last


@pytest.mark.django_db
def test_a_single_page_shows_no_pager(client, staff, url):
    Incident.objects.create(title="only", severity="critical")

    body = client.get(url).content.decode()

    assert "page-prev" not in body and "page-next" not in body
    assert "Page 1 of 1" not in body


def _busy_list(monkeypatch, suffix=""):
    monkeypatch.setattr(incident_queries, "PAGE_SIZE", 2)
    node = Node.objects.create(instance_id="web-1", hostname=f"web{suffix}")
    created = []
    for index in range(5):
        incident = Incident.objects.create(title=f"disk {index}{suffix}", severity="critical")
        _alert(incident, node)
        created.append(incident)
    return node, created


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_rendered_list_is_clean(client, staff, url, monkeypatch, label, headers):
    _busy_list(monkeypatch)

    body = client.get(f"{url}?page=2&since=bogus", **headers).content.decode()

    assert "c-badge" in body and "c-chip" in body and "c-notice" in body
    assert "page-prev" in body and "page-next" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_rendered_empty_list_is_clean(client, staff, url, label, headers):
    Incident.objects.create(title="open crit", severity="critical")

    body = client.get(f"{url}?severity=info", **headers).content.decode()

    assert "filters-clear" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_a_hostile_title_and_hostname_are_escaped(client, staff, url, monkeypatch, headers):
    node, _ = _busy_list(monkeypatch, suffix=f" {MARKUP}")

    body = client.get(url, **headers).content.decode()

    assert f"disk 4 {ESCAPED}" in body
    assert f"web {ESCAPED} · 1 alert" in body
    assert f">web {ESCAPED}</a>" in body
    assert f'id="chip-node-{node.pk}"' in body
    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_every_link_in_the_polled_region_has_a_unique_id(client, staff, url, monkeypatch, headers):
    node, created = _busy_list(monkeypatch)

    body = client.get(f"{url}?page=2", **headers).content.decode()
    region = body if headers else body[body.index('<div id="incidents"') : body.index("</main>")]

    links = re.findall(r"<a\b[^>]*>", region)
    ids = re.findall(r'\sid="([^"]*)"', region)
    expected = (
        [f"chip-status-{key}" for key in ("active", "open", "acknowledged", "resolved")]
        + ["chip-status-closed", "chip-status-all"]
        + [f"chip-severity-{key}" for key in ("any", "critical", "warning", "info")]
        + ["chip-node-any", f"chip-node-{node.pk}"]
        + [f"chip-since-{key}" for key in ("any", "24h", "7d", "30d")]
        + [f"incident-{incident.pk}" for incident in (created[2], created[1])]
        + ["page-prev", "page-next"]
    )
    assert [re.search(r'\sid="([^"]*)"', link).group(1) for link in links] == expected
    assert ids == ["incidents", *expected]
    assert len(ids) == len(set(ids))
    assert not re.search(r"<(?:button|input|select|textarea|details|summary)\b", region)
