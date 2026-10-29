import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN

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
