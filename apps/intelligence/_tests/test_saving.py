"""Saving a provider from the console, and which provider is on."""

import pytest

from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.saving import (
    FIELDS,
    StaleProvider,
    active_provider,
    provider_digest,
    save_provider,
)
from config.stale import Stale

pytestmark = pytest.mark.django_db


def row(provider):
    return IntelligenceProvider.objects.filter(pk=provider.pk).values(*FIELDS).get()


def make(name, **kwargs):
    return IntelligenceProvider.objects.create(name=name, provider="claude", **kwargs)


def active_names():
    return sorted(
        IntelligenceProvider.objects.filter(is_active=True).values_list("name", flat=True)
    )


def test_a_fresh_save_writes():
    provider = make("a", config={"model": "m"})
    values = dict(row(provider), description="new", config={"model": "n"})

    save_provider(provider.pk, values, expected=provider_digest(row(provider)))

    provider.refresh_from_db()
    assert (provider.description, provider.config) == ("new", {"model": "n"})


def test_a_stale_save_raises_stale_provider():
    provider = make("a")
    expected = provider_digest(row(provider))
    IntelligenceProvider.objects.filter(pk=provider.pk).update(description="someone else")

    with pytest.raises(StaleProvider):
        save_provider(provider.pk, dict(row(provider), description="mine"), expected=expected)
    assert issubclass(StaleProvider, Stale)
    assert row(provider)["description"] == "someone else"


def test_turning_one_on_turns_the_others_off():
    make("on", is_active=True)
    off = make("off")

    save_provider(off.pk, dict(row(off), is_active=True), expected=provider_digest(row(off)))

    assert active_names() == ["off"]


def test_saving_the_active_one_keeps_it_on():
    on = make("on", is_active=True)

    save_provider(on.pk, dict(row(on), description="x"), expected=provider_digest(row(on)))

    assert active_names() == ["on"]


def test_turning_one_off_leaves_the_others_alone():
    on = make("on", is_active=True)
    other = make("other")

    save_provider(other.pk, dict(row(other), description="x"), expected=provider_digest(row(other)))
    save_provider(on.pk, dict(row(on), is_active=False), expected=provider_digest(row(on)))

    assert active_names() == []
    assert row(other)["description"] == "x"


def test_a_stale_save_that_turns_one_on_leaves_the_active_one_on():
    make("on", is_active=True)
    off = make("off")
    expected = provider_digest(row(off))
    IntelligenceProvider.objects.filter(pk=off.pk).update(description="someone else")

    with pytest.raises(StaleProvider):
        save_provider(off.pk, dict(row(off), is_active=True), expected=expected)

    assert active_names() == ["on"]


def test_the_active_provider_is_the_one_on_or_none():
    assert active_provider() is None
    make("off")
    on = make("on", is_active=True)

    assert active_provider() == on
