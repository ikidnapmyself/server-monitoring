"""Read model for the routes page: the routing map, one card per route. Returns dataclasses."""

from dataclasses import dataclass

from apps.orchestration.routing import describe_match
from config.describing import Action
from config.netmap import get_map_context

STATE_TONES = {"ok": "ok", "inactive": "muted", "never-matches": "critical", "shadowed": "warning"}
MUTED_STATES = ("inactive", "shadowed")


@dataclass(frozen=True)
class RouteCard:
    pk: int
    name: str
    state: str
    state_words: str
    tone: str
    muted: bool
    conditions: tuple[str, ...]
    stages: tuple[str, ...]
    delivery: str
    delivery_action: Action | None
    seed_note: str
    edit: Action

    @property
    def anchor(self) -> str:
        return f"route-{self.pk}"


@dataclass(frozen=True)
class RouteList:
    cards: tuple[RouteCard, ...]
    summary: str


def _card(lane: dict) -> RouteCard:
    return RouteCard(
        pk=lane["pk"],
        name=lane["name"],
        state=lane["state"],
        state_words=lane["state_words"],
        tone=STATE_TONES[lane["state"]],
        muted=lane["state"] in MUTED_STATES,
        conditions=tuple(lane["conditions"]) or (describe_match([]),),
        stages=tuple(lane["stages"]),
        delivery=lane["delivery"]["words"],
        delivery_action=lane["delivery"]["action"],
        seed_note=lane["seed_note"],
        edit=lane["edit"],
    )


def build_route_list() -> RouteList:
    cards = tuple(_card(lane) for lane in get_map_context()["lanes"])
    count = len(cards)
    summary = (
        f"{count} route{'' if count == 1 else 's'}, evaluated top to bottom. "
        "The first that matches wins."
        if cards
        else ""
    )
    return RouteList(cards=cards, summary=summary)
