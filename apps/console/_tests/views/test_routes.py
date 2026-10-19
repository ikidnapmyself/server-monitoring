import re

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def _empty_table():
    clear_lanes()


@pytest.fixture
def url():
    return reverse("console:routes")


@pytest.fixture
def route_viewer(client, bare_staff):
    bare_staff.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="orchestration", codename="view_pipelinedefinition"
        )
    )
    client.force_login(bare_staff)
    return bare_staff


def _lane(name, priority, **kw):
    return PipelineDefinition.objects.create(name=name, priority=priority, **kw)


def _card(body, lane):
    start = body.index(f'<li id="route-{lane.pk}"')
    ends = [i for i in (body.find('<li id="route-', start + 1), body.find("</ol>", start)) if i > 0]
    return body[start : min(ends)]


def test_the_page_lives_at_routes(url):
    assert url == "/console/routes/"


def test_anonymous_is_sent_to_the_admin_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/routes/"


def test_staff_without_a_route_permission_is_refused(client, bare_staff, url):
    assert client.get(url).status_code == 403


def test_routes_render_in_match_order(client, superuser, url):
    client.force_login(superuser)
    _lane("late", 20, match=[])
    _lane("early", 10, match=[{"field": "source", "op": "is", "value": "cluster"}])

    body = client.get(url).content.decode()

    assert body.index("early") < body.index("late")
    assert "2 routes, evaluated top to bottom. The first that matches wins." in body
    assert "Source is cluster" in body
    assert "Matches everything" in body


def test_the_page_sits_under_system_with_its_subnav(client, superuser, url):
    client.force_login(superuser)

    body = client.get(url).content.decode()

    assert re.search(r'href="/console/routes/" aria-current="page">.*?<span>System</span>', body)
    assert re.search(
        r'<nav class="c-subnav"[^>]*>\s*<a [^>]*aria-current="page"[^>]*>Routes</a>', body
    )


def test_the_shadowed_route_names_its_shadower_and_is_muted(client, superuser, url):
    client.force_login(superuser)
    _lane("all", 1, match=[])
    later = _lane("later", 2, match=[{"field": "source", "op": "is", "value": "cluster"}])

    card = _card(client.get(url).content.decode(), later)

    assert "c-box--muted" in card
    assert "Never reached: all catches its traffic first" in card


def test_a_superuser_gets_the_links(client, superuser, url):
    client.force_login(superuser)
    ch = NotificationChannel.objects.create(name="ops", driver="email", is_active=True, config={})
    bound = _lane("bound", 1, match=[], stages=["notify"], channel=ch, tags={"seed_shape": "x"})
    bare = _lane("bare", 2, match=[], stages=["notify"])

    body = client.get(url).content.decode()

    bound_card = _card(body, bound)
    assert "Delivers to ops" in bound_card
    assert f'href="/console/channels/{ch.pk}/">Open ops</a>' in bound_card
    assert f'href="/console/routes/{bound.pk}/">Open route</a>' in bound_card
    assert "Set up without delivery. It delivers once a channel is active." in bound_card
    bare_card = _card(body, bare)
    assert "Can&#x27;t deliver: the matched route has no active channel" in bare_card
    assert f'href="/console/routes/{bare.pk}/">Give it an active channel</a>' in bare_card


def test_a_view_only_reader_can_open_a_route(client, route_viewer, url):
    lane = _lane("all", 1, match=[], stages=["check"])

    card = _card(client.get(url).content.decode(), lane)

    assert "Records only, sends nothing" in card
    assert f'href="/console/routes/{lane.pk}/">Open route</a>' in card


def test_a_view_only_reader_sees_the_route_fix_as_text(client, route_viewer, url):
    lane = _lane("bare", 1, match=[], stages=["notify"])

    card = _card(client.get(url).content.decode(), lane)

    assert "Give it an active channel" in card
    assert f'href="/console/routes/{lane.pk}/">Give it an active channel</a>' not in card


def test_a_route_viewer_sees_the_channel_as_text(client, route_viewer, url):
    ch = NotificationChannel.objects.create(name="ops", driver="email", is_active=True)
    lane = _lane("bound", 1, match=[], stages=["notify"], channel=ch)

    card = _card(client.get(url).content.decode(), lane)

    assert '<p class="c-description__action">Open ops</p>' in card
    assert f'href="/console/channels/{ch.pk}/"' not in card


def test_no_routes_says_so(client, superuser, url):
    client.force_login(superuser)

    body = client.get(url).content.decode()

    assert "No routes." in body
    assert "evaluated top to bottom" not in body


def test_htmx_gets_the_fragment(client, superuser, url):
    client.force_login(superuser)
    _lane("all", 1, match=[])

    body = client.get(url, **HTMX).content.decode()

    assert "<html" not in body
    assert "Matches everything" in body


def test_the_page_carries_nothing_the_csp_blocks(client, superuser, url):
    client.force_login(superuser)
    _lane("all", 1, match=[])

    body = client.get(url).content.decode()

    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_a_card_labels_its_conditions(client, superuser, url):
    client.force_login(superuser)
    lane = _lane("all", 1, match=[])

    card = _card(client.get(url).content.decode(), lane)

    assert '<span class="c-muted">Conditions</span>' in card
    assert ">Matches<" not in card


def test_a_lane_with_no_stages_says_records_only_once(client, superuser, url):
    client.force_login(superuser)
    lane = _lane("bare", 1, match=[], stages=[])

    card = _card(client.get(url).content.decode(), lane)

    assert card.count("Records only") == 1
    assert "No downstream stages" in card


def test_a_route_adder_gets_the_add_button(client, superuser, url):
    client.force_login(superuser)

    body = client.get(url).content.decode()

    assert '<a id="routes-add" class="c-button c-button--sm" href="/console/routes/new/">' in body


def test_a_view_only_reader_gets_no_add_button(client, route_viewer, url):
    assert "Add a route" not in client.get(url).content.decode()


def test_a_route_only_reader_gets_system_on_routes_and_no_channels_tab(client, route_viewer, url):
    body = client.get(url).content.decode()

    assert re.search(r'href="/console/routes/" aria-current="page">.*?<span>System</span>', body)
    assert ">Routes</a>" in body
    assert 'href="/console/channels/"' not in body
    assert ">Channels</a>" not in body


def test_a_reader_of_neither_sees_no_system_item(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert "<span>System</span>" not in body
    assert 'href="/console/routes/"' not in body
    assert 'href="/console/channels/"' not in body
