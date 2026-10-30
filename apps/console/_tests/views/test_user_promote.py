import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def bob():
    return User.objects.create_user("bob", password="x")


def _url(user):
    return reverse("console:user-make-superuser", args=[user.pk])


def test_the_url():
    assert reverse("console:user-make-superuser", args=[7]) == "/console/users/7/make-superuser/"


def test_an_anonymous_post_logs_in_back_to_the_user(client, bob):
    resp = client.post(_url(bob))

    assert resp["Location"] == f"{reverse('console:login')}?next=/console/users/{bob.pk}/"
    bob.refresh_from_db()
    assert bob.is_superuser is False


def test_active_staff_who_arent_superusers_are_refused(client, active_staff, bob):
    assert client.post(_url(bob)).status_code == 403
    bob.refresh_from_db()
    assert bob.is_superuser is False


def test_a_post_makes_a_superuser(client, superuser, bob):
    resp = client.post(_url(bob))

    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:user", args=[bob.pk])
    assert [str(m) for m in get_messages(resp.wsgi_request)] == ["bob is now a superuser."]
    bob.refresh_from_db()
    assert (bob.is_superuser, bob.is_staff) == (True, True)


def test_a_get_is_refused(client, superuser, bob):
    assert client.get(_url(bob)).status_code == 405
    bob.refresh_from_db()
    assert bob.is_superuser is False


def test_a_missing_user_is_the_console_404_page(client, superuser):
    resp = client.post(reverse("console:user-make-superuser", args=[999]))

    assert resp.status_code == 404
    assert "This user doesn't exist. It may have been deleted." in resp.content.decode()


@pytest.mark.parametrize(
    ("flags", "warning"),
    [
        ({"is_active": False}, "bob is inactive, so they can't be made a superuser."),
        ({"is_superuser": True, "is_staff": True}, "bob is already a superuser."),
    ],
    ids=["inactive", "already"],
)
def test_a_refused_promotion_writes_nothing_and_says_why(client, superuser, flags, warning):
    bob = User.objects.create_user("bob", password="x", **flags)

    resp = client.post(_url(bob))

    assert resp.status_code == 303
    assert resp["Location"] == reverse("console:user", args=[bob.pk])
    assert [str(m) for m in get_messages(resp.wsgi_request)] == [warning]
    stored = User.objects.get(pk=bob.pk)
    assert (stored.is_active, stored.is_superuser) == (
        flags.get("is_active", True),
        flags.get("is_superuser", False),
    )
