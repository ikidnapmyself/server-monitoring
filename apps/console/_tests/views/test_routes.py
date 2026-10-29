import re

import pytest
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


def test_routes_render_in_match_order(client, superuser, url):
    _lane("late", 20, match=[])
    _lane("early", 10, match=[{"field": "source", "op": "is", "value": "cluster"}])

    body = client.get(url).content.decode()

    assert body.index("early") < body.index("late")
    assert "2 routes, evaluated top to bottom. The first that matches wins." in body
    assert "Source is cluster" in body
    assert "Matches everything" in body


def test_the_page_sits_under_system_with_its_subnav(client, superuser, url):

    body = client.get(url).content.decode()

    assert re.search(r'href="/console/routes/" aria-current="page">.*?<span>System</span>', body)
    assert re.search(
        r'<nav class="c-subnav"[^>]*>\s*<a [^>]*aria-current="page"[^>]*>Routes</a>', body
    )


def test_the_shadowed_route_names_its_shadower_and_is_muted(client, superuser, url):
    _lane("all", 1, match=[])
    later = _lane("later", 2, match=[{"field": "source", "op": "is", "value": "cluster"}])

    card = _card(client.get(url).content.decode(), later)

    assert "c-row--muted" in card
    assert '<span class="c-row__icon c-tone-warning">' in card
    assert "Never reached: all catches its traffic first" in card


def test_a_superuser_gets_the_links(client, superuser, url):
    ch = NotificationChannel.objects.create(name="ops", driver="email", is_active=True, config={})
    bound = _lane("bound", 1, match=[], stages=["notify"], channel=ch, tags={"seed_shape": "x"})
    bare = _lane("bare", 2, match=[], stages=["notify"])

    body = client.get(url).content.decode()

    bound_card = _card(body, bound)
    assert "Delivers to ops" in bound_card
    assert f'href="/console/channels/{ch.pk}/">Open ops</a>' in bound_card
    assert (
        f'<a id="route-{bound.pk}-edit" href="/console/routes/{bound.pk}/">bound</a>' in bound_card
    )
    assert '<span class="c-row__icon c-tone-ok">' in bound_card
    assert "Set up without delivery. It delivers once a channel is active." in bound_card
    bare_card = _card(body, bare)
    assert (
        '<span class="c-tone-warning">'
        "Can&#x27;t deliver: the matched route has no active channel</span>"
    ) in bare_card
    assert '<span class="c-row__icon c-tone-warning">' in bare_card
    assert f'href="/console/routes/{bare.pk}/">Give it an active channel</a>' in bare_card


def test_a_route_name_opens_the_route(client, superuser, url):
    lane = _lane("all", 1, match=[], stages=["check"])

    card = _card(client.get(url).content.decode(), lane)

    assert "Records only, sends nothing" in card
    assert f'href="/console/routes/{lane.pk}/">all</a>' in card


def test_no_routes_says_so(client, superuser, url):

    body = client.get(url).content.decode()

    assert "No routes." in body
    assert "evaluated top to bottom" not in body


def test_htmx_gets_the_fragment(client, superuser, url):
    _lane("all", 1, match=[])

    body = client.get(url, **HTMX).content.decode()

    assert "<html" not in body
    assert "Matches everything" in body


def test_the_page_carries_nothing_the_csp_blocks(client, superuser, url):
    _lane("all", 1, match=[])

    body = client.get(url).content.decode()

    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_a_row_says_its_conditions_in_one_line(client, superuser, url):
    lane = _lane(
        "web",
        1,
        match=[
            {"field": "source", "op": "is", "value": "cluster"},
            {"field": "source", "op": "is-not", "value": "grafana"},
        ],
    )

    card = _card(client.get(url).content.decode(), lane)

    assert '<div class="c-row__line">Source is cluster and Source is not grafana</div>' in card
    assert ">Matches<" not in card


def test_a_lane_with_no_stages_says_records_only_once(client, superuser, url):
    lane = _lane("bare", 1, match=[], stages=[])

    card = _card(client.get(url).content.decode(), lane)

    assert card.count("Records only") == 1
    assert "No downstream stages" in card


def test_a_route_adder_gets_the_add_button(client, superuser, url):

    body = client.get(url).content.decode()

    head = body[body.index('<div class="c-pagehead">') : body.index('<div id="routes">')]
    assert (
        '<a id="routes-add" class="c-button c-button--sm c-button--primary" '
        'href="/console/routes/new/">'
    ) in head
