from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError, OperationalError
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries.users import make_superuser, set_password

pytestmark = pytest.mark.django_db

PASSWORD = "pw-correct-horse-9"
User = get_user_model()


@pytest.fixture
def amy():
    return User.objects.create_user("amy", password="old-password-9")


def _url(user):
    return reverse("console:user-password", args=[user.pk])


def _set(password=PASSWORD, confirm=None):
    return {"new_password1": password, "new_password2": password if confirm is None else confirm}


def test_the_password_url():
    assert reverse("console:user-password", args=[7]) == "/console/users/7/password/"


def test_anonymous_is_sent_to_the_console_login(client, amy):
    resp = client.post(_url(amy), _set())

    assert resp["Location"] == f"{reverse('console:login')}?next={_url(amy)}"
    amy.refresh_from_db()
    assert amy.check_password("old-password-9")


def test_the_page_asks_for_the_new_password_twice(client, superuser, amy):
    resp = client.get(_url(amy))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/user_password.html"
    assert "<title>Set password · amy · Console</title>" in body
    assert 'name="new_password1"' in body and 'name="new_password2"' in body
    assert f'<a id="user-password-back" class="c-crumb" href="/console/users/{amy.pk}/">' in body
    assert 'href="/console/users/" aria-current="page">Users</a>' in body
    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_setting_anothers_password(client, superuser, amy):
    resp = client.post(_url(amy), _set())

    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:user", args=[amy.pk])
    assert [str(m) for m in get_messages(resp.wsgi_request)] == ["Password set for amy."]
    amy.refresh_from_db()
    assert amy.check_password(PASSWORD)


def test_setting_your_own_keeps_you_signed_in(client, superuser):
    resp = client.post(_url(superuser), _set())

    assert resp.status_code == 303
    superuser.refresh_from_db()
    assert superuser.check_password(PASSWORD)
    assert client.get(reverse("console:overview")).status_code == 200


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (_set(confirm="something-else-9"), "The two password fields didn"),
        (_set("12345678"), "too common"),
    ],
    ids=["mismatch", "weak"],
)
def test_an_invalid_password_is_refused(client, superuser, amy, data, error):
    resp = client.post(_url(amy), data)

    assert resp.status_code == 200
    assert error in resp.content.decode().replace("&#x27;", "'")
    amy.refresh_from_db()
    assert amy.check_password("old-password-9")


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_user_is_the_console_404_page(client, superuser, method):
    resp = getattr(client, method)(reverse("console:user-password", args=[999]))

    assert resp.status_code == 404
    assert "This user doesn't exist. It may have been deleted." in resp.content.decode()


def test_htmx_gets_the_form_without_the_layout(client, superuser, amy):
    resp = client.get(_url(amy), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_user_password.html"


@pytest.mark.parametrize("error", [OperationalError("locked"), IntegrityError("constraint")])
def test_a_busy_database_sets_nothing_and_says_so(client, superuser, amy, error):
    with mock.patch("apps.console.views.user_password.set_password", side_effect=error):
        resp = client.post(_url(amy), _set())

    assert resp.status_code == 409
    assert "Couldn&#x27;t set the password just now. Nothing changed, so try again." in (
        resp.content.decode()
    )
    amy.refresh_from_db()
    assert amy.check_password("old-password-9")


def test_a_promotion_after_the_form_was_built_survives(client, superuser, amy):
    def promote_then_set(pk, raw):
        make_superuser(pk)
        return set_password(pk, raw)

    with mock.patch("apps.console.views.user_password.set_password", side_effect=promote_then_set):
        resp = client.post(_url(amy), _set())

    assert resp.status_code == 303
    amy.refresh_from_db()
    assert amy.is_superuser is True
    assert amy.check_password(PASSWORD)


def test_a_user_deleted_before_the_save_stays_deleted(client, superuser, amy):
    def delete_then_set(pk, raw):
        User.objects.filter(pk=pk).delete()
        return set_password(pk, raw)

    with mock.patch("apps.console.views.user_password.set_password", side_effect=delete_then_set):
        resp = client.post(_url(amy), _set())

    assert resp.status_code == 404
    assert not User.objects.filter(pk=amy.pk).exists()
