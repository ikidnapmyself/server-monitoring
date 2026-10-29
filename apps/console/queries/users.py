"""Read models for the user pages, and the rules for removing a user's access."""

from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.forms import ModelForm
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from apps.console.access import admits
from apps.console.forms import UserCreateForm, UserEditForm, UserPasswordForm
from config.describing import Action, Fact

LAST_SUPERUSER = "At least one active superuser must remain."
GONE = "This user was deleted."


@dataclass(frozen=True)
class UserRow:
    pk: int
    username: str
    name: str
    email: str
    last_login: datetime | None
    active: bool
    superuser: bool
    view: Action

    @property
    def anchor(self) -> str:
        return f"user-{self.pk}"

    @property
    def label(self) -> str:
        if not self.active:
            return "Inactive"
        return "Active" if self.superuser else "Not a superuser"

    @property
    def tone(self) -> str:
        if not self.active:
            return "muted"
        return "ok" if self.superuser else "warning"

    @property
    def promote(self) -> Action | None:
        if self.superuser or not self.active:
            return None
        return Action("Make superuser", "make_superuser", {"pk": self.pk})


@dataclass(frozen=True)
class UserList:
    rows: tuple[UserRow, ...]

    @property
    def add(self) -> Action:
        return Action("Add a user", "add_user", {})


@dataclass(frozen=True)
class UserEditor:
    pk: int | None
    username: str
    form: UserCreateForm | UserEditForm
    url: str
    back_url: str
    facts: tuple[Fact, ...]

    @property
    def password(self) -> Action | None:
        if self.pk is None:
            return None
        return Action("Set password", "set_user_password", {"pk": self.pk})


@dataclass(frozen=True)
class PasswordEditor:
    pk: int
    username: str
    form: UserPasswordForm
    url: str
    back_url: str


def build_user_list() -> UserList:
    rows = tuple(
        UserRow(
            pk=user.pk,
            username=user.username,
            name=user.get_full_name(),
            email=user.email,
            last_login=user.last_login,
            active=user.is_active,
            superuser=user.is_superuser,
            view=Action(user.username, "view_user", {"pk": user.pk}),
        )
        for user in get_user_model().objects.order_by("username", "pk")
    )
    return UserList(rows=rows)


def _when(moment) -> str:
    return date_format(timezone.localtime(moment), "Y-m-d H:i")


def build_user_editor(pk: int | None, data=None) -> UserEditor | None:
    """The editor for user ``pk``, or the form for a new user when ``pk`` is None."""
    users = reverse("console:users")
    if pk is None:
        return UserEditor(
            pk=None,
            username="",
            form=UserCreateForm(data),
            url=reverse("console:user-new"),
            back_url=users,
            facts=(),
        )
    user = get_user_model().objects.filter(pk=pk).first()
    if user is None:
        return None
    return UserEditor(
        pk=user.pk,
        username=user.username,
        form=UserEditForm(data, instance=user),
        url=reverse("console:user", args=[user.pk]),
        back_url=f"{users}#user-{user.pk}",
        facts=(
            Fact("Joined", _when(user.date_joined)),
            Fact("Last signed in", _when(user.last_login) if user.last_login else "Never"),
        ),
    )


def build_password_editor(pk: int, data=None) -> PasswordEditor | None:
    user = get_user_model().objects.filter(pk=pk).first()
    if user is None:
        return None
    return PasswordEditor(
        pk=user.pk,
        username=user.username,
        form=UserPasswordForm(user, data),
        url=reverse("console:user-password", args=[user.pk]),
        back_url=reverse("console:user", args=[user.pk]),
    )


def removal_refusal(actor, user, verb: str) -> str:
    """Why ``actor`` can't ``verb`` ``user`` ("deactivate" or "delete"), or "" when they can.

    Call it inside the transaction that writes, so the superuser rows stay locked until the
    write lands and two removals can't both see another superuser left.
    """
    if admits(user):
        with transaction.atomic():
            others = (
                get_user_model()
                .objects.select_for_update()
                .filter(is_active=True, is_superuser=True)
                .exclude(pk=user.pk)
            )
            if not list(others.values_list("pk", flat=True)):
                return LAST_SUPERUSER
    if user.pk == actor.pk:
        return f"You can't {verb} your own account."
    return ""


def update_user(actor, form: ModelForm) -> str:
    """Save a valid edit form, unless it deactivates someone who must stay. Returns why not."""
    with transaction.atomic():
        stored = get_user_model().objects.select_for_update().filter(pk=form.instance.pk).first()
        if stored is None:
            return GONE
        if stored.is_active and not form.cleaned_data["is_active"]:
            refusal = removal_refusal(actor, stored, "deactivate")
            if refusal:
                return refusal
        form.save()
    return ""
