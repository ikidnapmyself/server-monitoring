import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.console.forms import REFUSED

PASSWORD = "pw-correct-horse"


def _user(**flags):
    return get_user_model().objects.create_user("op", password=PASSWORD, **flags)


def _sign_in(client, password=PASSWORD, **extra):
    return client.post(reverse("console:login"), {"username": "op", "password": password, **extra})


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
    assert 'class="c-unav"' not in body
    assert "Content-Security-Policy" in resp


@pytest.mark.django_db
def test_staff_signs_in_and_lands_on_next(client):
    _user(is_staff=True)

    resp = _sign_in(client, next="/console/incidents/?q=disk")

    assert resp.status_code == 302
    assert resp["Location"] == "/console/incidents/?q=disk"
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_staff_without_next_lands_on_the_overview(client):
    _user(is_staff=True)

    assert _sign_in(client)["Location"] == reverse("console:overview")


@pytest.mark.django_db
def test_a_foreign_next_lands_on_the_overview(client):
    _user(is_staff=True)

    assert _sign_in(client, next="https://evil.example/")["Location"] == reverse("console:overview")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("flags", "password"),
    [
        ({}, PASSWORD),
        ({"is_staff": True, "is_active": False}, PASSWORD),
        ({"is_staff": True}, "wrong"),
    ],
    ids=["not-staff", "inactive", "wrong-password"],
)
def test_every_refusal_reads_the_same(client, flags, password):
    _user(**flags)

    resp = _sign_in(client, password=password)

    assert resp.status_code == 200
    assert REFUSED in resp.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_logout_signs_out_and_returns_to_the_login(client, staff):
    resp = client.post(reverse("console:logout"))

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:login")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_logout_refuses_a_get(client, staff):
    assert client.get(reverse("console:logout")).status_code == 405
