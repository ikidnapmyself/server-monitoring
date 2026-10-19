import pytest

from apps.console.queries.routes import build_route_list
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes
from config.describing import Action

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _empty_table():
    clear_lanes()


def _lane(name, priority, **kw):
    return PipelineDefinition.objects.create(name=name, priority=priority, **kw)


def test_one_card_per_route_in_match_order():
    _lane("late", 20, match=[])
    _lane("early", 10, match=[{"field": "source", "op": "is", "value": "cluster"}])

    routes = build_route_list()

    assert [card.name for card in routes.cards] == ["early", "late"]
    assert routes.summary == "2 routes, evaluated top to bottom. The first that matches wins."


def test_one_route_is_singular():
    _lane("only", 1, match=[])
    assert build_route_list().summary == (
        "1 route, evaluated top to bottom. The first that matches wins."
    )


def test_no_routes_has_no_summary():
    routes = build_route_list()
    assert routes.cards == ()
    assert routes.summary == ""
    assert routes.can_add is False


def test_an_adder_is_offered_the_new_route_page():
    routes = build_route_list(can_add=True)
    assert routes.can_add is True
    assert routes.add == Action("Add a route", "add_route", {})


def test_a_card_carries_the_map_in_words_and_actions():
    ch = NotificationChannel.objects.create(name="ops", driver="email", is_active=True, config={})
    lane = _lane(
        "cluster",
        1,
        match=[{"field": "source", "op": "is", "value": "cluster"}],
        stages=["check", "notify"],
        channel=ch,
        tags={"seed_shape": "record-only"},
    )

    card = build_route_list().cards[0]

    assert card.pk == lane.pk
    assert card.anchor == f"route-{lane.pk}"
    assert (card.state, card.state_words, card.tone, card.muted) == ("ok", "", "ok", False)
    assert card.conditions == ("Source is cluster",)
    assert card.stages == ("Diagnose", "Notify")
    assert card.delivery == "Delivers to ops"
    assert card.delivery_action == Action("Open ops", "view_channel", {"pk": ch.pk, "name": "ops"})
    assert card.seed_note.startswith("Set up without delivery.")
    assert card.edit == Action("Open route", "view_route", {"pk": lane.pk})


def test_a_catch_all_says_it_matches_everything():
    _lane("all", 1, match=[])
    assert build_route_list().cards[0].conditions == ("Matches everything",)


@pytest.mark.parametrize(
    ("kw", "state", "tone", "muted"),
    [
        ({"is_active": False}, "inactive", "muted", True),
        (
            {"match": [{"field": "source", "op": "zap", "value": "x"}]},
            "never-matches",
            "critical",
            False,
        ),
    ],
)
def test_a_route_that_takes_no_traffic_says_so(kw, state, tone, muted):
    _lane("odd", 1, **{"match": [], **kw})
    card = build_route_list().cards[0]
    assert (card.state, card.tone, card.muted) == (state, tone, muted)
    assert card.state_words


def test_a_shadowed_route_is_muted_and_names_its_shadower():
    _lane("all", 1, match=[])
    _lane("later", 2, match=[{"field": "source", "op": "is", "value": "cluster"}])

    card = build_route_list().cards[1]

    assert (card.state, card.tone, card.muted) == ("shadowed", "warning", True)
    assert card.state_words == "Never reached: all catches its traffic first"
