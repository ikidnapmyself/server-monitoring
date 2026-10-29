from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.console.queries.users import (
    LAST_SUPERUSER,
    build_password_editor,
    build_user_deletion,
    build_user_editor,
    build_user_list,
    delete_user,
    make_superuser,
    removal_refusal,
    update_user,
)
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


def test_an_active_user_who_isnt_a_superuser_can_be_promoted():
    user = _user("staff", is_staff=True)

    (row,) = build_user_list().rows

    assert (row.label, row.tone) == ("Not a superuser", "warning")
    assert row.promote == Action("Make superuser", "make_superuser", {"pk": user.pk})


def test_an_inactive_user_who_isnt_a_superuser_reads_inactive_and_isnt_promoted():
    _user("gone", is_active=False)

    (row,) = build_user_list().rows

    assert (row.label, row.tone) == ("Inactive", "muted")
    assert row.promote is None


def test_the_list_carries_the_add_action():
    assert build_user_list().add == Action("Add a user", "add_user", {})


def _superuser(username, **kw):
    return get_user_model().objects.create_superuser(username, password="x", **kw)


class TestRemovalRefusal:
    def test_another_active_superuser_can_be_removed(self):
        actor = _superuser("root")

        assert removal_refusal(actor, _superuser("amy"), "delete") == ""

    def test_you_cant_remove_yourself(self):
        actor = _superuser("root")
        _superuser("amy")

        assert (
            removal_refusal(actor, actor, "deactivate") == "You can't deactivate your own account."
        )
        assert removal_refusal(actor, actor, "delete") == "You can't delete your own account."

    def test_the_last_active_superuser_must_remain(self):
        actor = _superuser("root")
        _superuser("gone", is_active=False)

        assert removal_refusal(actor, actor, "deactivate") == LAST_SUPERUSER

    def test_someone_who_isnt_an_active_superuser_can_always_be_removed(self):
        actor = _superuser("root")

        assert removal_refusal(actor, _user("bob", is_staff=True), "delete") == ""
        assert removal_refusal(actor, _superuser("gone", is_active=False), "delete") == ""


class TestTheEditor:
    def test_a_missing_user_is_none(self):
        assert build_user_editor(999) is None
        assert build_password_editor(999) is None

    def test_a_new_user(self):
        editor = build_user_editor(None)

        assert (editor.pk, editor.username, editor.facts) == (None, "", ())
        assert editor.url == "/console/users/new/"
        assert editor.back_url == "/console/users/"
        assert editor.password is None
        assert "password1" in editor.form.fields

    def test_an_existing_user(self):
        user = _user("amy", last_login=timezone.now())

        editor = build_user_editor(user.pk)

        assert (editor.pk, editor.username) == (user.pk, "amy")
        assert editor.url == f"/console/users/{user.pk}/"
        assert editor.back_url == f"/console/users/#user-{user.pk}"
        assert [f.label for f in editor.facts] == ["Joined", "Last signed in"]
        assert editor.facts[1].value != "Never"
        assert editor.password == Action("Set password", "set_user_password", {"pk": user.pk})
        assert "is_active" in editor.form.fields

    def test_a_user_who_never_signed_in(self):
        assert build_user_editor(_user("amy").pk).facts[1].value == "Never"

    def test_the_password_editor(self):
        user = _user("amy")

        editor = build_password_editor(user.pk)

        assert (editor.pk, editor.username) == (user.pk, "amy")
        assert editor.url == f"/console/users/{user.pk}/password/"
        assert editor.back_url == f"/console/users/{user.pk}/"
        assert list(editor.form.fields) == ["new_password1", "new_password2"]


def _edit(user, **changes):
    data = {
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_active": "on" if user.is_active else "",
        **changes,
    }
    editor = build_user_editor(user.pk, data)
    assert editor.form.is_valid(), editor.form.errors
    return editor.form


class TestUpdateUser:
    def test_a_change_is_saved(self):
        actor = _superuser("root")
        amy = _user("amy")

        assert update_user(actor, _edit(amy, email="amy@example.com")) == ""

        amy.refresh_from_db()
        assert amy.email == "amy@example.com"

    def test_deactivating_another_superuser_is_saved(self):
        actor = _superuser("root")
        amy = _superuser("amy")

        assert update_user(actor, _edit(amy, is_active="")) == ""

        amy.refresh_from_db()
        assert amy.is_active is False

    def test_deactivating_yourself_is_refused_and_changes_nothing(self):
        actor = _superuser("root")
        _superuser("amy")

        refusal = update_user(actor, _edit(actor, is_active="", email="new@example.com"))

        assert refusal == "You can't deactivate your own account."
        actor.refresh_from_db()
        assert (actor.is_active, actor.email) == (True, "")

    def test_deactivating_the_last_active_superuser_is_refused(self):
        actor = _superuser("root")
        _superuser("gone", is_active=False)

        assert update_user(actor, _edit(actor, is_active="")) == LAST_SUPERUSER
        actor.refresh_from_db()
        assert actor.is_active is True

    def test_a_user_deleted_before_the_save_is_not_recreated(self):
        actor = _superuser("root")
        amy = _user("amy")
        form = _edit(amy, email="amy@example.com")
        get_user_model().objects.filter(pk=amy.pk).delete()

        assert update_user(actor, form) != ""
        assert not get_user_model().objects.filter(username="amy").exists()


class TestTheUserPageActions:
    def test_a_non_superuser_can_be_promoted_and_deleted(self):
        actor = _superuser("root")
        bob = _user("bob")

        editor = build_user_editor(bob.pk, actor=actor)

        assert editor.promote == Action("Make superuser", "make_superuser", {"pk": bob.pk})
        assert editor.delete == Action("Delete user", "delete_user", {"pk": bob.pk})

    def test_a_superuser_has_no_promote(self):
        actor = _superuser("root")

        assert build_user_editor(_superuser("amy").pk, actor=actor).promote is None

    def test_an_inactive_user_has_no_promote(self):
        actor = _superuser("root")

        assert build_user_editor(_user("gone", is_active=False).pk, actor=actor).promote is None

    def test_you_cant_delete_yourself_so_there_is_no_delete(self):
        actor = _superuser("root")
        _superuser("amy")

        assert build_user_editor(actor.pk, actor=actor).delete is None

    def test_without_an_actor_there_is_no_delete(self):
        assert build_user_editor(_user("bob").pk).delete is None

    def test_a_new_user_has_neither(self):
        editor = build_user_editor(None, actor=_superuser("root"))

        assert (editor.promote, editor.delete) == (None, None)


class TestDeletion:
    def test_a_missing_user_is_none(self):
        assert build_user_deletion(_superuser("root"), 999) is None

    def test_the_deletion_says_when_they_last_signed_in(self):
        seen = timezone.now()
        bob = _user("bob", last_login=seen)

        deletion = build_user_deletion(_superuser("root"), bob.pk)

        assert (deletion.pk, deletion.username, deletion.last_login) == (bob.pk, "bob", seen)
        assert deletion.refusal == ""
        assert deletion.url == f"/console/users/{bob.pk}/delete/"
        assert deletion.back_url == f"/console/users/{bob.pk}/"

    def test_the_deletion_carries_the_refusal(self):
        actor = _superuser("root")

        assert build_user_deletion(actor, actor.pk).refusal == LAST_SUPERUSER

    def test_delete_user_deletes(self):
        actor = _superuser("root")
        bob = _user("bob")

        assert delete_user(actor, bob.pk) == ""
        assert not get_user_model().objects.filter(pk=bob.pk).exists()

    def test_delete_user_refuses_and_keeps_the_user(self):
        actor = _superuser("root")
        _superuser("amy")

        assert delete_user(actor, actor.pk) == "You can't delete your own account."
        assert get_user_model().objects.filter(pk=actor.pk).exists()

    def test_delete_user_on_a_missing_user_is_none(self):
        assert delete_user(_superuser("root"), 999) is None


class TestMakeSuperuser:
    def test_it_sets_both_flags_and_names_the_user(self):
        bob = _user("bob")

        assert make_superuser(bob.pk) == "bob"
        bob.refresh_from_db()
        assert (bob.is_superuser, bob.is_staff) == (True, True)

    def test_a_missing_user_is_none(self):
        assert make_superuser(999) is None
