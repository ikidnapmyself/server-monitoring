import pytest
from django.contrib.auth import get_user_model
from django.http import QueryDict

from apps.console.filters import ActivityFilter
from apps.console.models import AuditEntry
from apps.console.queries import activity
from apps.console.queries.activity import build_activity_list
from apps.orchestration.models import PipelineDefinition
from config.describing import Action

pytestmark = pytest.mark.django_db

User = get_user_model()


def _entry(action="route.edit", kind="route", pk=1, name="web", actor=None, **fields):
    return AuditEntry.objects.create(
        action=action,
        target_kind=kind,
        target_id=pk,
        target_name=name,
        actor=actor,
        actor_name=actor.username if actor else "",
        **fields,
    )


def _list(**params):
    query = QueryDict(mutable=True)
    query.update(params)
    return build_activity_list(ActivityFilter.parse(query))


def test_entries_are_newest_first_and_worded():
    first = _entry(name="old")
    second = _entry(name="new")

    listing = _list()

    assert [row.pk for row in listing.rows] == [second.pk, first.pk]
    assert listing.rows[0].words.headline == "Edited route new"
    assert (listing.total, listing.page, listing.pages, listing.empty) == (2, 1, 1, "")


def test_an_existing_target_links_and_a_gone_one_is_text():
    route = PipelineDefinition.objects.create(name="web", stages=["notify"], match=[])
    _entry(pk=route.pk)
    _entry(pk=route.pk + 1000, name="gone")

    gone, there = _list().rows

    assert there.words.target == Action("route web", "view_route", {"pk": route.pk})
    assert gone.words.target == Action("route gone")


def test_a_kind_with_no_model_or_no_id_is_never_looked_up():
    _entry(kind="widget", pk=3)
    _entry(action="session.sign_in_failed", kind="user", pk=None, name="mallory")

    assert [row.words.headline for row in _list().rows] == [
        "Failed sign-in as mallory",
        "Edited record web",
    ]


def test_a_deleted_actor_keeps_their_name():
    amy = User.objects.create_user("amy")
    _entry(actor=amy)
    amy.delete()

    (row,) = _list().rows

    assert row.actor == Action("amy")


def test_filters_by_actor_and_by_kind():
    amy = User.objects.create_user("amy")
    bob = User.objects.create_user("bob")
    mine = _entry(actor=amy)
    _entry(actor=bob)
    key = _entry(action="key.edit", kind="key", actor=bob)

    assert [r.pk for r in _list(actor=str(amy.pk)).rows] == [mine.pk]
    assert [r.pk for r in _list(kind="key").rows] == [key.pk]
    assert _list(actor=str(amy.pk)).actor == amy.pk
    assert _list(kind="key").kind == "key"
    assert _list().actors == ((amy.pk, "amy"), (bob.pk, "bob"))


def test_a_stored_kind_this_hub_doesnt_word_still_filters():
    odd = _entry(kind="widget")
    _entry()

    assert [r.pk for r in _list(kind="widget").rows] == [odd.pk]


def test_an_invalid_filter_is_dropped_and_said():
    _entry()

    listing = _list(kind="nope", actor="x")

    assert listing.total == 1
    assert listing.notice == "Ignored invalid filter: actor, kind."


def test_the_kind_choices_are_words():
    kinds = dict(_list().kinds)

    assert kinds["key"] == "API key"
    assert kinds["route"] == "Route"


def test_no_entries_and_no_match_are_told_apart():
    assert _list().empty == "none"
    _entry()
    assert _list(kind="key").empty == "no_match"


def test_it_paginates(monkeypatch):
    monkeypatch.setattr(activity, "PAGE_SIZE", 2)
    entries = [_entry(name=str(i)) for i in range(5)]

    first = _list(kind="route")
    last = _list(kind="route", page="3")
    beyond = _list(page="9")

    assert [r.pk for r in first.rows] == [entries[4].pk, entries[3].pk]
    assert (first.pages, first.prev_url, first.next_url) == (
        3,
        "",
        "/console/activity/?kind=route&page=2",
    )
    assert [r.pk for r in last.rows] == [entries[0].pk]
    assert (last.prev_url, last.next_url) == ("/console/activity/?kind=route&page=2", "")
    assert beyond.page == 3
    assert first.url == "/console/activity/?kind=route"
