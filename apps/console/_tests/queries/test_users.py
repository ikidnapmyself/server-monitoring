from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.console.queries.users import build_user_list
from config.describing import Action

pytestmark = pytest.mark.django_db


def _user(username, **kw):
    return get_user_model().objects.create_user(username, password="x", **kw)


def test_rows_are_ordered_by_username():
    _user("zed")
    _user("amy")

    assert [row.username for row in build_user_list().rows] == ["amy", "zed"]


def test_an_active_superuser_row():
    seen = timezone.now() - timedelta(hours=2)
    user = _user(
        "amy",
        first_name="Amy",
        last_name="Pond",
        email="amy@example.com",
        is_superuser=True,
        is_staff=True,
        last_login=seen,
    )

    (row,) = build_user_list().rows

    assert (row.pk, row.username, row.name, row.email) == (
        user.pk,
        "amy",
        "Amy Pond",
        "amy@example.com",
    )
    assert row.last_login == seen
    assert (row.label, row.tone) == ("Active", "ok")
    assert row.view == Action("amy", "view_user", {"pk": user.pk})
    assert row.promote is None
    assert row.anchor == f"user-{user.pk}"


def test_an_inactive_superuser_row():
    _user("old", is_superuser=True, is_active=False)

    (row,) = build_user_list().rows

    assert (row.label, row.tone) == ("Inactive", "muted")
    assert row.last_login is None
    assert row.name == ""
    assert row.promote is None


def test_a_user_who_isnt_a_superuser_cant_sign_in_and_can_be_promoted():
    user = _user("staff", is_staff=True)

    (row,) = build_user_list().rows

    assert (row.label, row.tone) == ("Can't sign in", "warning")
    assert row.promote == Action("Make superuser", "make_superuser", {"pk": user.pk})


def test_an_inactive_user_who_isnt_a_superuser_reads_inactive():
    _user("gone", is_active=False)

    (row,) = build_user_list().rows

    assert (row.label, row.tone) == ("Inactive", "muted")
    assert row.promote is not None


def test_the_list_carries_the_add_action():
    assert build_user_list().add == Action("Add a user", "add_user", {})
