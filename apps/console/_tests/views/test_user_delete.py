from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries.users import LAST_SUPERUSER

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def amy():
    return User.objects.create_user(
        "amy", password="x", last_login=timezone.now() - timedelta(days=3)
    )


def _url(user):
    return reverse("console:user-delete", args=[user.pk])


def _messages(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


def test_the_delete_url():
    assert reverse("console:user-delete", args=[7]) == "/console/users/7/delete/"


def test_an_anonymous_delete_logs_in_back_to_the_user(client, amy):
    resp = client.post(_url(amy))

    assert resp["Location"] == f"{reverse('console:login')}?next=/console/users/{amy.pk}/"
    assert User.objects.filter(pk=amy.pk).exists()


def test_active_staff_who_arent_superusers_are_refused(client, active_staff, amy):
    assert client.post(_url(amy)).status_code == 403
    assert User.objects.filter(pk=amy.pk).exists()


def test_the_page_confirms_and_says_when_they_last_signed_in(client, superuser, amy):
    resp = client.get(_url(amy))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/user_delete.html"
    assert "<title>Delete user · amy · Console</title>" in body
    assert "Delete user amy?" in body
    assert "They last signed in <time" in body
    assert "3\xa0days ago</time>" in body
    assert 'id="user-delete-confirm"' in body
    assert f'<a id="user-delete-back" class="c-crumb" href="/console/users/{amy.pk}/">' in body
    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_a_user_who_never_signed_in_says_so(client, superuser):
    bob = User.objects.create_user("bob", password="x")

    assert "They never signed in." in client.get(_url(bob)).content.decode()


def test_a_delete_removes_the_user(client, superuser, amy):
    resp = client.post(_url(amy))

    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:users")
    assert _messages(resp) == ["User amy deleted."]
    assert not User.objects.filter(pk=amy.pk).exists()


def test_deleting_yourself_is_refused_on_get_and_post(client, superuser):
    User.objects.create_superuser("other", password="x")

    page = client.get(_url(superuser)).content.decode()
    resp = client.post(_url(superuser))

    assert "You can&#x27;t delete your own account." in page
    assert 'id="user-delete-confirm"' not in page
    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:user", args=[superuser.pk])
    assert _messages(resp) == ["You can't delete your own account."]
    assert User.objects.filter(pk=superuser.pk).exists()


def test_deleting_the_last_active_superuser_is_refused_on_get_and_post(client, superuser):
    User.objects.create_superuser("gone", password="x", is_active=False)

    page = client.get(_url(superuser)).content.decode()
    resp = client.post(_url(superuser))

    assert LAST_SUPERUSER in page
    assert 'id="user-delete-confirm"' not in page
    assert resp.status_code == 303
    assert _messages(resp) == [LAST_SUPERUSER]
    assert User.objects.filter(pk=superuser.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_user_is_the_console_404_page(client, superuser, method):
    resp = getattr(client, method)(reverse("console:user-delete", args=[999]))

    assert resp.status_code == 404
    assert "This user doesn't exist. It may have been deleted." in resp.content.decode()


def test_a_user_deleted_during_the_post_is_the_404(client, superuser, amy):
    with mock.patch("apps.console.views.user_delete.delete_user", return_value=None):
        resp = client.post(_url(amy))

    assert resp.status_code == 404


def test_htmx_gets_the_confirmation_without_the_layout(client, superuser, amy):
    resp = client.get(_url(amy), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_user_delete.html"
