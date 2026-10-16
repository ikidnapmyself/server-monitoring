import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import named_incident
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


def _nav(body, label):
    """The opening tag of the underline nav tab named ``label``."""
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


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
def test_staff_gets_the_full_page_with_the_nav_tab_current(client, staff, url):
    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "<html" in body
    assert resp.templates[0].name == "console/incidents.html"
    assert "<title>Incidents · Console</title>" in body
    assert _nav(body, "Incidents") == f'<a class="c-unav__item" href="{url}" aria-current="page">'
    assert _nav(body, "Overview") == (
        f'<a class="c-unav__item" href="{reverse("console:overview")}">'
    )
    assert "script-src 'self'" in resp["Content-Security-Policy"]


@pytest.mark.django_db
def test_the_overview_nav_links_to_the_list_without_marking_it_current(client, staff, url):
    body = client.get(reverse("console:overview")).content.decode()

    overview = reverse("console:overview")
    assert _nav(body, "Incidents") == f'<a class="c-unav__item" href="{url}">'
    assert _nav(body, "Overview") == (
        f'<a class="c-unav__item" href="{overview}" aria-current="page">'
    )


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial_rooted_at_the_polled_element(client, staff, url):
    resp = client.get(url, **HTMX)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "<html" not in body
    assert resp.templates[0].name == "console/partials/_incidents.html"
    assert "console/base.html" not in [t.name for t in resp.templates]
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
def test_rows_show_state_title_label_nodes_count_and_age(client, staff, url):
    incident = Incident.objects.create(instance="web-1", severity="critical")
    for name, key in [("web-1", "Swap low"), ("web-2", "Disk full")]:
        _alert(incident, Node.objects.create(instance_id=name, hostname=name), key=key)
    lonely = Incident.objects.create(severity="warning", status="acknowledged")

    body = client.get(url).content.decode()

    page_url = reverse("console:incident", args=[incident.pk])
    assert f'<a id="incident-{incident.pk}" href="{page_url}">Disk full on web-1</a>' in body
    assert '<span class="c-label c-tone-critical">critical</span>' in body
    assert '<span class="c-label c-tone-warning">warning</span>' in body
    assert re.search(r'class="c-box__row [^"]*c-status--open"', body)
    assert re.search(r'class="c-box__row [^"]*c-status--acknowledged"', body)
    assert '<span class="c-sr-only">open</span>' in body
    assert '<span class="c-sr-only">acknowledged</span>' in body
    assert re.search(r'<span class="c-host">.*?</svg>web-1 \+1</span> · #\d+ opened', body)
    assert re.search(
        rf'<div class="c-row__meta"><span class="c-host c-host--unknown">.*?</svg>Unknown host'
        rf'</span> · #{lonely.pk} opened <time datetime="[^"]+" title="[^"]+">[^<]+ ago</time>'
        r" · 0 alerts</div>",
        body,
    )
    assert f'id="incident-{lonely.pk}"' in body
    assert "2 incidents" in body
    assert "No incidents" not in body


@pytest.mark.django_db
def test_a_single_alert_and_a_single_incident_read_in_the_singular(client, staff, url):
    incident = Incident.objects.create(severity="info")
    _alert(incident, None)

    body = client.get(url).content.decode()

    assert re.search(r"</time> · 1 alert</div>", body)
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
    named_incident("open crit", severity="critical")
    named_incident("acked warn", severity="warning", status="acknowledged")
    named_incident("resolved info", severity="info", status="resolved")

    body = client.get(f"{url}{query}").content.decode()

    shown = {title for title in ("open crit", "acked warn", "resolved info") if title in body}
    assert shown == expected


@pytest.mark.django_db
def test_the_node_filter_narrows_the_rows(client, staff, url):
    node = Node.objects.create(instance_id="web-1", hostname="web-1")
    _alert(Incident.objects.create(severity="critical"), node, "on the node")
    named_incident("elsewhere", severity="critical")

    body = client.get(f"{url}?node={node.pk}").content.decode()

    assert "on the node" in body
    assert "elsewhere" not in body
    assert 'aria-current="true"' in _tag(body, f"chip-node-{node.pk}")
    assert 'aria-current="true"' not in _tag(body, "chip-node-any")


@pytest.mark.django_db
def test_the_since_filter_narrows_the_rows(client, staff, url):
    old = named_incident("last month", severity="critical")
    Incident.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=20))
    named_incident("today", severity="critical")

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
def test_the_box_header_counts_each_status_under_the_other_filters(client, staff, url):
    Incident.objects.create(severity="critical")
    Incident.objects.create(severity="critical", status="resolved")
    Incident.objects.create(severity="info", status="resolved")

    body = client.get(f"{url}?severity=critical").content.decode()

    header = body[body.index('<nav class="c-states"') :]
    header = header[: header.index("</nav>")]
    counts = dict(
        re.findall(r'id="chip-status-(\w+)".*?<span class="c-counter">(\d+)</span></a>', header)
    )
    assert counts == {
        "active": "1",
        "open": "1",
        "acknowledged": "0",
        "resolved": "1",
        "closed": "0",
        "all": "2",
    }
    assert 'aria-label="Status"' in header


@pytest.mark.django_db
def test_the_node_chips_are_left_out_when_no_node_exists(client, staff, url):
    body = client.get(url).content.decode()

    assert "chip-node-" not in body
    assert 'aria-label="Node"' not in body
    assert body.count('aria-current="true"') == 3


@pytest.mark.django_db
def test_invalid_filters_are_named_in_a_notice(client, staff, url):
    named_incident("still listed", severity="critical")

    body = client.get(f"{url}?status=bogus&node=999&page=x").content.decode()

    assert re.search(
        r'<div class="c-flash c-tone-warning" role="status"><svg\b.*?</svg>'
        r"<span>Ignored invalid filter: status, node, page\.</span></div>",
        body,
    )
    assert "still listed" in body
    assert f'hx-get="{url}"' in _tag(body, "incidents")


@pytest.mark.django_db
def test_valid_filters_show_no_notice(client, staff, url):
    body = client.get(f"{url}?status=all").content.decode()

    assert '<div class="c-flash' not in body
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
    Incident.objects.create(severity="critical")

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
def test_default_filters_with_only_finished_incidents_say_none_are_active(client, staff, url):
    Incident.objects.create(severity="critical", status="resolved")

    body = client.get(url).content.decode()

    assert "No active incidents." in body
    assert "No incidents match these filters." not in body
    assert "No incidents yet." not in body
    assert "filters-clear" not in body


@pytest.mark.django_db
def test_pager_links_keep_the_filters(client, staff, url, monkeypatch):
    monkeypatch.setattr(incident_queries, "PAGE_SIZE", 1)
    for index in range(3):
        Incident.objects.create(severity="critical")

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
        Incident.objects.create(severity="critical")

    first = client.get(url).content.decode()
    last = client.get(f"{url}?page=2").content.decode()

    assert "page-next" in first and "page-prev" not in first
    assert "page-prev" in last and "page-next" not in last


@pytest.mark.django_db
def test_a_single_page_shows_no_pager(client, staff, url):
    Incident.objects.create(severity="critical")

    body = client.get(url).content.decode()

    assert "page-prev" not in body and "page-next" not in body
    assert "Page 1 of 1" not in body


SYNC = 'hx-sync="#incidents:replace"'


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "count"),
    [("?page=2", 18), ("?severity=info", 17)],
    ids=["chips-and-pager", "chips-and-clear"],
)
def test_every_swapping_link_replaces_the_request_in_flight(
    client, staff, url, monkeypatch, query, count
):
    _busy_list(monkeypatch)

    body = client.get(f"{url}{query}", **HTMX).content.decode()

    swapping = [tag for tag in re.findall(r"<[a-z]+\b[^>]*>", body) if "hx-get=" in tag]
    root, links = swapping[0], swapping[1:]
    assert root.startswith('<div id="incidents"')
    # The polled root keeps htmx's default, so a poll never aborts a click.
    assert "hx-sync" not in root
    assert len(links) == count
    assert all(link.startswith("<a ") and SYNC in link for link in links)
    assert body.count(SYNC) == count


def _busy_list(monkeypatch, suffix=""):
    monkeypatch.setattr(incident_queries, "PAGE_SIZE", 2)
    node = Node.objects.create(instance_id="web-1", hostname=f"web{suffix}")
    created = []
    for index in range(5):
        incident = Incident.objects.create(severity="critical")
        _alert(incident, node, key=f"disk {index}{suffix}")
        created.append(incident)
    return node, created


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_rendered_list_is_clean(client, staff, url, monkeypatch, label, headers):
    _busy_list(monkeypatch)

    body = client.get(f"{url}?page=2&since=bogus", **headers).content.decode()

    assert "c-label" in body and "c-seg" in body and '<div class="c-flash' in body
    assert "page-prev" in body and "page-next" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_rendered_empty_list_is_clean(client, staff, url, label, headers):
    Incident.objects.create(severity="critical")

    body = client.get(f"{url}?severity=info", **headers).content.decode()

    assert "filters-clear" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_a_hostile_title_and_hostname_are_escaped(client, staff, url, monkeypatch, headers):
    node, _ = _busy_list(monkeypatch, suffix=f" {MARKUP}")

    body = client.get(url, **headers).content.decode()

    assert f"disk 4 {ESCAPED}" in body
    assert f"</svg>web {ESCAPED}</span> · #" in body
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
        [f"chip-severity-{key}" for key in ("any", "critical", "warning", "info")]
        + ["chip-node-any", f"chip-node-{node.pk}"]
        + [f"chip-since-{key}" for key in ("any", "24h", "7d", "30d")]
        + [f"chip-status-{key}" for key in ("active", "open", "acknowledged", "resolved")]
        + ["chip-status-closed", "chip-status-all"]
        + [f"incident-{incident.pk}" for incident in (created[2], created[1])]
        + ["page-prev", "page-next"]
    )
    assert [re.search(r'\sid="([^"]*)"', link).group(1) for link in links] == expected
    assert ids == ["incidents", *expected]
    assert len(ids) == len(set(ids))
    assert not re.search(r"<(?:button|input|select|textarea|details|summary)\b", region)


def _labelled(incident, key, **labels):
    return Alert.objects.create(
        fingerprint=f"{incident.pk}:{key}",
        name=key,
        severity="warning",
        status="firing",
        source="cluster",
        labels=labels,
        incident=incident,
        started_at=timezone.now(),
    )


@pytest.mark.django_db
def test_a_row_names_the_host_from_alert_labels_when_no_node_is_linked(client, staff, url):
    incident = Incident.objects.create(severity="critical")
    _labelled(incident, "a", hostname="db-7")

    body = client.get(url).content.decode()

    assert re.search(r'<span class="c-host">.*?</svg>db-7</span> · #\d+ opened', body)


@pytest.mark.django_db
@pytest.mark.parametrize("label", ["instance_id", "instance", "hostname"])
def test_the_node_filter_matches_alerts_that_name_the_node_only_in_labels(
    client, staff, url, label
):
    node = Node.objects.create(instance_id="i-db7", hostname="db-7")
    name = "i-db7" if label != "hostname" else "db-7"
    mine = Incident.objects.create()
    _labelled(mine, "a", **{label: name})
    theirs = Incident.objects.create()
    _labelled(theirs, "a", hostname="web-1")
    linked = Incident.objects.create()
    _alert(linked, node)

    body = client.get(f"{url}?node={node.pk}").content.decode()

    assert f'id="incident-{mine.pk}"' in body
    assert f'id="incident-{linked.pk}"' in body
    assert f'id="incident-{theirs.pk}"' not in body


@pytest.mark.django_db
def test_a_label_naming_the_node_does_not_override_a_link_to_another_node(client, staff, url):
    node = Node.objects.create(instance_id="db-7", hostname="db-7")
    other = Node.objects.create(instance_id="web-1", hostname="web-1")
    incident = Incident.objects.create()
    Alert.objects.create(
        fingerprint="x",
        name="x",
        source="cluster",
        labels={"hostname": "db-7"},
        node=other,
        incident=incident,
        started_at=timezone.now(),
    )

    body = client.get(f"{url}?node={node.pk}").content.decode()

    assert f'id="incident-{incident.pk}"' not in body
