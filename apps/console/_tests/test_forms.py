import pytest
from django.contrib.auth import get_user_model

from apps.console.forms import UserCreateForm, UserEditForm, UserPasswordForm

pytestmark = pytest.mark.django_db

PASSWORD = "pw-correct-horse-9"


def _create(**overrides):
    data = {
        "username": "amy",
        "email": "amy@example.com",
        "first_name": "Amy",
        "last_name": "Pond",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    return UserCreateForm({**data, **overrides})


def test_a_new_user_is_an_active_superuser_with_a_usable_password():
    form = _create()
    assert form.is_valid(), form.errors

    user = form.save()

    user.refresh_from_db()
    assert (user.is_active, user.is_staff, user.is_superuser) == (True, True, True)
    assert user.check_password(PASSWORD)
    assert (user.email, user.first_name, user.last_name) == ("amy@example.com", "Amy", "Pond")


def test_an_unsaved_new_user_already_carries_the_flags():
    form = _create()
    assert form.is_valid(), form.errors

    user = form.save(commit=False)

    assert user.pk is None
    assert (user.is_active, user.is_staff, user.is_superuser) == (True, True, True)


def test_the_create_form_asks_for_names_email_and_the_password_twice():
    assert list(_create().fields) == [
        "username",
        "email",
        "first_name",
        "last_name",
        "password1",
        "password2",
    ]


def test_a_password_the_validators_refuse_is_an_error():
    form = _create(password1="12345678", password2="12345678")

    assert not form.is_valid()
    assert "password2" in form.errors


def test_the_edit_form_has_no_password_and_carries_active():
    user = get_user_model().objects.create_user("amy", password="x")

    form = UserEditForm(instance=user)

    assert list(form.fields) == ["username", "email", "first_name", "last_name", "is_active"]
    assert form.fields["is_active"].label == "Active"


def test_the_password_form_sets_a_validated_password():
    user = get_user_model().objects.create_user("amy", password="x")
    form = UserPasswordForm(user, {"new_password1": PASSWORD, "new_password2": PASSWORD})
    assert form.is_valid(), form.errors

    form.save()

    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.parametrize("username", ["AMY", "amy"], ids=["other-case", "same"])
def test_the_edit_form_refuses_another_users_name_in_any_case(username):
    get_user_model().objects.create_user("amy", password="x")
    bob = get_user_model().objects.create_user("bob", password="x")

    form = UserEditForm({"username": username, "is_active": "on"}, instance=bob)

    assert not form.is_valid()
    assert form.errors["username"] == ["A user with that username already exists."]


def test_the_edit_form_lets_a_user_keep_or_recase_their_own_name():
    amy = get_user_model().objects.create_user("amy", password="x")

    assert UserEditForm({"username": "Amy", "is_active": "on"}, instance=amy).is_valid()


def test_the_edit_form_refuses_a_fullwidth_copy_of_another_users_name():
    get_user_model().objects.create_user("amy", password="x")
    bob = get_user_model().objects.create_user("bob", password="x")

    form = UserEditForm({"username": "\uff21\uff2d\uff39", "is_active": "on"}, instance=bob)

    assert not form.is_valid()
    assert form.errors["username"] == ["A user with that username already exists."]
