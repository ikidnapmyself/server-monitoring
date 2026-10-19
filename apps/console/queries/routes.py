"""Read models for the routes page, one card per route, and for one route's editor."""

import json
from dataclasses import dataclass

from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from apps.orchestration.forms import RouteForm
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.routing import fallthrough, representable
from config.describing import Action, Fact
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
    can_add: bool = False

    @property
    def add(self) -> Action:
        return Action("Add a route", "add_route", {})


def _card(lane: dict) -> RouteCard:
    return RouteCard(
        pk=lane["pk"],
        name=lane["name"],
        state=lane["state"],
        state_words=lane["state_words"],
        tone=STATE_TONES[lane["state"]],
        muted=lane["state"] in MUTED_STATES,
        conditions=tuple(lane["conditions"]),
        stages=tuple(lane["stages"]),
        delivery=lane["delivery"]["words"],
        delivery_action=lane["delivery"]["action"],
        seed_note=lane["seed_note"],
        edit=lane["edit"],
    )


def build_route_list(*, can_add: bool = False) -> RouteList:
    cards = tuple(_card(lane) for lane in get_map_context()["lanes"])
    count = len(cards)
    summary = (
        f"{count} route{'' if count == 1 else 's'}, evaluated top to bottom. "
        "The first that matches wins."
        if cards
        else ""
    )
    return RouteList(cards=cards, summary=summary, can_add=can_add)


@dataclass(frozen=True)
class RouteEditor:
    pk: int | None
    name: str
    form: RouteForm
    version: str
    url: str
    back_url: str
    represented: bool
    stored_conditions: tuple[str, ...]
    facts: tuple[Fact, ...]
    can_change: bool

    @property
    def delete(self) -> Action | None:
        if self.pk is None:
            return None
        return Action("Delete route", "delete_route", {"pk": self.pk})


@dataclass(frozen=True)
class RouteDeletion:
    pk: int
    name: str
    consequence: str
    url: str
    back_url: str


def build_route_deletion(pk: int) -> RouteDeletion | None:
    lane = PipelineDefinition.objects.filter(pk=pk).first()
    if lane is None:
        return None
    return RouteDeletion(
        pk=lane.pk,
        name=lane.name,
        consequence=fallthrough(lane),
        url=reverse("console:route-delete", args=[lane.pk]),
        back_url=reverse("console:route", args=[lane.pk]),
    )


def _when(moment) -> str:
    return date_format(timezone.localtime(moment), "Y-m-d H:i")


def _facts(lane: PipelineDefinition) -> tuple[Fact, ...]:
    facts = [Fact("Created by", lane.created_by)] if lane.created_by else []
    facts += [
        Fact("Created", _when(lane.created_at)),
        Fact("Updated", _when(lane.updated_at)),
        Fact("Version", str(lane.version)),
    ]
    if lane.tags:
        facts.append(Fact("Tags", json.dumps(lane.tags, sort_keys=True)))
    return tuple(facts)


def _stored(match) -> tuple[str, ...]:
    if not isinstance(match, list):
        return (json.dumps(match, sort_keys=True),)
    return tuple(json.dumps(cond, sort_keys=True) for cond in match)


def build_route_editor(
    pk: int | None, data=None, *, can_change: bool, version: str | None = None
) -> RouteEditor | None:
    """The editor for route ``pk``, or for a new route when ``pk`` is None.

    ``version`` keeps the one a re-rendered submit carried, so it can still be refused as stale.
    """
    lane = None
    if pk is not None:
        lane = PipelineDefinition.objects.filter(pk=pk).first()
        if lane is None:
            return None
    form = RouteForm(data, instance=lane, disabled=not can_change)
    routes = reverse("console:routes")
    if lane is None:
        return RouteEditor(
            pk=None,
            name="",
            form=form,
            version="1",
            url=reverse("console:route-new"),
            back_url=routes,
            represented=True,
            stored_conditions=(),
            facts=(),
            can_change=can_change,
        )
    represented = representable(lane.match)
    return RouteEditor(
        pk=lane.pk,
        name=lane.name,
        form=form,
        version=str(lane.version) if version is None else version,
        url=reverse("console:route", args=[lane.pk]),
        back_url=f"{routes}#route-{lane.pk}",
        represented=represented,
        stored_conditions=() if represented else _stored(lane.match),
        facts=_facts(lane),
        can_change=can_change,
    )
