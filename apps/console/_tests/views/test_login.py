from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.console.forms import REFUSED
from apps.console.views.login import ConsoleLoginView
from config.middleware import rate_limit

WINDOW = 1_800_000_040.0
PASSWORD = "pw-correct-horse"


def _user(**flags):
    return get_user_model().objects.create_user("op", password=PASSWORD, **flags)


def _sign_in(client, password=PASSWORD, **extra):
    return client.post(reverse("console:login"), {"username": "op", "password": password, **extra})


def test_the_login_and_logout_urls():
    assert reverse("console:login") == "/console/login/"
    assert reverse("console:logout") == "/console/logout/"


@pytest.mark.django_db
def test_the_login_page_renders_for_anyone(client):
    resp = client.get(reverse("console:login"))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Sign in to the console" in body
    assert 'name="username"' in body
    assert 'name="password"' in body
    assert 'name="csrfmiddlewaretoken"' in body
    assert reverse("console:logout") not in body
    assert "Sign out" not in body
    assert 'class="c-unav"' not in body
    assert "Content-Security-Policy" in resp


@pytest.mark.django_db
def test_a_superuser_signs_in_and_lands_on_next(client):
    _user(is_staff=True, is_superuser=True)

    resp = _sign_in(client, next="/console/incidents/?q=disk")

    assert resp.status_code == 302
    assert resp["Location"] == "/console/incidents/?q=disk"
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_a_superuser_without_next_lands_on_the_overview(client):
    _user(is_superuser=True)

    assert _sign_in(client)["Location"] == reverse("console:overview")
    assert client.get(reverse("console:overview")).status_code == 200


@pytest.mark.django_db
def test_a_foreign_next_lands_on_the_overview(client):
    _user(is_superuser=True)

    assert _sign_in(client, next="https://evil.example/")["Location"] == reverse("console:overview")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("flags", "password"),
    [
        ({"is_staff": True}, PASSWORD),
        ({}, PASSWORD),
        ({"is_superuser": True, "is_staff": True, "is_active": False}, PASSWORD),
        ({"is_superuser": True}, "wrong"),
    ],
    ids=["staff", "plain", "inactive-superuser", "wrong-password"],
)
def test_every_refusal_reads_the_same(client, flags, password):
    _user(**flags)

    resp = _sign_in(client, password=password)

    assert resp.status_code == 200
    assert REFUSED in resp.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_the_refusal_is_announced(client):
    _user()

    body = _sign_in(client).content.decode()

    assert f'<p class="c-field__error c-tone-critical" role="alert">{REFUSED}</p>' in body


@pytest.mark.django_db
def test_a_signed_in_superuser_skips_the_login(client, superuser):
    resp = client.get(reverse("console:login"))

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:overview")


@pytest.mark.django_db
def test_a_signed_in_superuser_sent_back_to_the_login_lands_on_the_overview(client, superuser):
    resp = client.get(reverse("console:login"), {"next": reverse("console:login")})

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:overview")


@pytest.mark.django_db
def test_a_sign_in_whose_next_is_the_login_lands_on_the_overview(client):
    _user(is_superuser=True)

    assert _sign_in(client, next="/console/login/?x=1")["Location"] == reverse("console:overview")


@pytest.mark.django_db
def test_logout_signs_out_and_returns_to_the_login(client, superuser):
    resp = client.post(reverse("console:logout"))

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:login")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_logout_refuses_a_get(client, superuser):
    assert client.get(reverse("console:logout")).status_code == 405
    assert "_auth_user_id" in client.session


@pytest.fixture
def limited(monkeypatch):
    # A pinned clock keeps all eleven attempts in one window.
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: WINDOW))
    with override_settings(
        RATE_LIMIT_ENABLED=True,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ):
        cache.clear()
        yield
        cache.clear()


@pytest.mark.django_db
def test_sign_in_attempts_are_rate_limited_but_the_page_is_not(client, limited):
    limit = settings.RATE_LIMITS["/console/login/"]
    assert limit == 10
    for _ in range(limit):
        assert _sign_in(client, password="wrong").status_code == 200

    refused = _sign_in(client, password="wrong", next="/console/runs/")

    assert refused.status_code == 429
    assert refused["Retry-After"] == "20"
    body = refused.content.decode()
    assert "Too many sign-in attempts. Try again in 20 seconds." in body
    assert 'role="alert"' in body
    assert 'name="password"' in body
    assert 'value="/console/runs/"' in body
    assert 'name="username" value="op"' in body
    assert client.get(reverse("console:login")).status_code == 200


@pytest.mark.parametrize(("retry_after", "wait"), [(1, "1 second"), (2, "2 seconds")])
def test_the_wait_reads_in_plain_words(rf, retry_after, wait):
    request = rf.post("/console/login/")
    request.user = AnonymousUser()

    body = ConsoleLoginView.rate_limited(request, retry_after).content.decode()

    assert f"Try again in {wait}." in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("next_url", "kept"),
    [("/console/runs/", "/console/runs/"), ("https://evil.example/", "")],
    ids=["local", "foreign"],
)
def test_the_rate_limited_page_keeps_only_a_local_next(rf, next_url, kept):
    request = rf.post("/console/login/", {"username": "op", "next": next_url})
    request.user = AnonymousUser()

    body = ConsoleLoginView.rate_limited(request, 5).content.decode()

    assert f'<input type="hidden" name="next" value="{kept}">' in body
