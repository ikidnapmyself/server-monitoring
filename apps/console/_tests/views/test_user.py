from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError, OperationalError
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries.users import LAST_SUPERUSER, build_user_editor

pytestmark = pytest.mark.django_db

PASSWORD = "pw-correct-horse-9"
User = get_user_model()


@pytest.fixture
def amy():
    return User.objects.create_user(
        "amy", password="x", email="amy@example.com", first_name="Amy", last_name="Pond"
    )


def _url(user=None):
    return reverse("console:user", args=[user.pk]) if user else reverse("console:user-new")


def _new(**overrides):
    data = {
        "username": "dan",
        "email": "dan@example.com",
        "first_name": "Dan",
        "last_name": "Lee",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    return {**data, **overrides}


def _edit(user, **changes):
    data = {
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }
    if user.is_active:
        data["is_active"] = "on"
    data.update(changes)
    return data


def _messages(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


def test_the_user_urls():
    assert reverse("console:user-new") == "/console/users/new/"
    assert reverse("console:user", args=[7]) == "/console/users/7/"


def test_anonymous_is_sent_to_the_console_login(client, amy):
    resp = client.get(_url(amy))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next={_url(amy)}"


def test_an_anonymous_save_logs_in_back_to_the_editor(client, amy):
    resp = client.post(_url(amy), _edit(amy, email="x@example.com"))

    assert resp["Location"] == f"{reverse('console:login')}?next={_url(amy)}"


def test_an_anonymous_add_logs_in_back_to_the_new_user_page(client):
    resp = client.post(_url(), _new())

    assert resp["Location"] == f"{reverse('console:login')}?next={_url()}"
    assert not User.objects.filter(username="dan").exists()


def test_active_staff_who_arent_superusers_are_refused(client, active_staff, amy):
    assert client.post(_url(amy), _edit(amy, email="x@example.com")).status_code == 403
    amy.refresh_from_db()
    assert amy.email == "amy@example.com"


class TestANewUser:
    def test_it_asks_for_names_email_and_the_password_twice(self, client, superuser):
        resp = client.get(_url())

        body = resp.content.decode()
        assert resp.status_code == 200
        assert resp.templates[0].name == "console/user.html"
        assert "<title>New user · Console</title>" in body
        for name in ("username", "email", "first_name", "last_name", "password1", "password2"):
            assert f'name="{name}"' in body, name
        assert 'name="is_active"' not in body
        assert 'id="user-save"' in body and "Create user" in body
        assert 'href="/console/users/" aria-current="page">Users</a>' in body
        assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]

    def test_a_valid_post_makes_an_active_superuser(self, client, superuser):
        resp = client.post(_url(), _new())

        user = User.objects.get(username="dan")
        assert resp.status_code == 303
        assert resp["Location"] == _url(user)
        assert (user.is_active, user.is_staff, user.is_superuser) == (True, True, True)
        assert user.has_usable_password() and user.check_password(PASSWORD)
        assert _messages(resp) == ["User dan added."]

    @pytest.mark.parametrize(
        ("overrides", "field", "error"),
        [
            ({"username": "amy"}, "username", "A user with that username already exists."),
            ({"password2": "something-else-9"}, "password2", "The two password fields didn"),
            ({"password1": "12345678", "password2": "12345678"}, "password2", "too common"),
        ],
        ids=["duplicate", "mismatch", "weak"],
    )
    def test_an_invalid_post_shows_the_error_and_makes_nothing(
        self, client, superuser, amy, overrides, field, error
    ):
        before = User.objects.count()

        resp = client.post(_url(), _new(**overrides))

        body = resp.content.decode()
        assert resp.status_code == 200
        assert error in body.replace("&#x27;", "'")
        assert User.objects.count() == before

    @pytest.mark.parametrize("error", [OperationalError("locked"), IntegrityError("unique")])
    def test_a_busy_database_adds_nothing(self, client, superuser, error):
        with mock.patch("apps.console.forms.UserCreateForm.save", side_effect=error):
            resp = client.post(_url(), _new())

        assert resp.status_code == 409
        assert "Couldn&#x27;t add the user just now." in resp.content.decode()
        assert not User.objects.filter(username="dan").exists()


class TestAnExistingUser:
    def test_it_shows_the_edit_form(self, client, superuser, amy):
        resp = client.get(_url(amy))

        body = resp.content.decode()
        assert resp.status_code == 200
        assert "<title>User amy · Console</title>" in body
        for name in ("username", "email", "first_name", "last_name", "is_active"):
            assert f'name="{name}"' in body, name
        assert 'name="password1"' not in body
        assert 'value="amy@example.com"' in body
        assert (
            f'<a id="user-password" class="c-button c-button--sm" href="{_url(amy)}password/">'
            in body
        )
        assert f'<a id="user-back" class="c-crumb" href="/console/users/#user-{amy.pk}">' in body
        assert "Last signed in" in body
        assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]

    def test_a_valid_post_saves_and_comes_back(self, client, superuser, amy):
        resp = client.post(_url(amy), _edit(amy, email="pond@example.com", is_active=""))

        assert resp.status_code == 303
        assert resp["Location"] == _url(amy)
        amy.refresh_from_db()
        assert (amy.email, amy.is_active) == ("pond@example.com", False)
        assert _messages(resp) == ["User amy saved."]
        assert "User amy saved." in client.get(_url(amy)).content.decode()

    def test_an_invalid_post_shows_the_error(self, client, superuser, amy):
        resp = client.post(_url(amy), _edit(amy, username=""))

        assert resp.status_code == 200
        assert "This field is required." in resp.content.decode()
        amy.refresh_from_db()
        assert amy.username == "amy"

    def test_deactivating_yourself_is_refused(self, client, superuser, amy):
        User.objects.create_superuser("other", password="x")

        resp = client.post(_url(superuser), _edit(superuser, is_active=""))

        assert resp.status_code == 409
        assert "You can&#x27;t deactivate your own account." in resp.content.decode()
        superuser.refresh_from_db()
        assert superuser.is_active is True

    def test_deactivating_the_last_active_superuser_is_refused(self, client, superuser):
        User.objects.create_superuser("gone", password="x", is_active=False)

        resp = client.post(_url(superuser), _edit(superuser, is_active="", email="x@example.com"))

        body = resp.content.decode()
        assert resp.status_code == 409
        assert LAST_SUPERUSER in body
        superuser.refresh_from_db()
        assert (superuser.is_active, superuser.email) == (True, "")

    @pytest.mark.parametrize("error", [OperationalError("locked"), IntegrityError("unique")])
    def test_a_busy_database_saves_nothing(self, client, superuser, amy, error):
        with mock.patch("apps.console.views.user.update_user", side_effect=error):
            resp = client.post(_url(amy), _edit(amy, email="x@example.com"))

        assert resp.status_code == 409
        assert "Couldn&#x27;t save just now." in resp.content.decode()

    def test_a_user_deleted_during_the_save_is_the_404(self, client, superuser, amy):
        editors = [build_user_editor(amy.pk, _edit(amy)), None]

        with (
            mock.patch("apps.console.views.user.build_user_editor", side_effect=editors),
            mock.patch("apps.console.views.user.update_user", return_value="gone"),
        ):
            resp = client.post(_url(amy), _edit(amy))

        assert resp.status_code == 404


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_user_is_the_console_404_page(client, superuser, method):
    resp = getattr(client, method)(reverse("console:user", args=[999]))

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This user doesn't exist. It may have been deleted." in body
    assert '<a id="not-found-back" href="/console/users/">Back to users</a>' in body
    assert '<a class="c-unav__item" href="/console/routes/" aria-current="page">' in body


def test_htmx_gets_the_editor_without_the_layout(client, superuser, amy):
    resp = client.get(_url(amy), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_user.html"
    assert "<html" not in resp.content.decode()


def test_the_user_page_offers_make_superuser_and_delete_for_a_non_superuser(client, superuser, amy):
    body = client.get(_url(amy)).content.decode()

    assert f'<form method="post" action="/console/users/{amy.pk}/make-superuser/">' in body
    assert 'id="user-promote"' in body
    assert (
        f'<a id="user-delete" class="c-button c-button--sm c-button--danger" '
        f'href="/console/users/{amy.pk}/delete/">Delete user</a>'
    ) in body


def test_your_own_page_offers_neither(client, superuser):
    body = client.get(_url(superuser)).content.decode()

    assert 'id="user-promote"' not in body
    assert 'id="user-delete"' not in body
