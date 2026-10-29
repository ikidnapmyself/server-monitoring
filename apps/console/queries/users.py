"""Read models for the users page."""

from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import get_user_model

from config.describing import Action


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
        return "Active" if self.superuser else "Can't sign in"

    @property
    def tone(self) -> str:
        if not self.active:
            return "muted"
        return "ok" if self.superuser else "warning"

    @property
    def promote(self) -> Action | None:
        if self.superuser:
            return None
        return Action("Make superuser", "make_superuser", {"pk": self.pk})


@dataclass(frozen=True)
class UserList:
    rows: tuple[UserRow, ...]

    @property
    def add(self) -> Action:
        return Action("Add a user", "add_user", {})


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
