"""Tests for the audit entries sign-ins and sign-outs record."""

import secrets

import pytest
from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.urls import reverse

from apps.console.models import AuditEntry

PASSWORD = secrets.token_urlsafe(16)

pytestmark = pytest.mark.django_db


def _sign_in(client, username="op", password=PASSWORD):
    return client.post(
        reverse("console:login"),
        {"username": username, "password": password},
        REMOTE_ADDR="192.0.2.8",
    )


@pytest.fixture
def op():
    return get_user_model().objects.create_superuser("op", password=PASSWORD)


def test_a_sign_in_is_recorded(client, op):
    _sign_in(client)

    entry = AuditEntry.objects.get()
    assert entry.action == "session.sign_in"
    assert (entry.actor, entry.actor_name) == (op, "op")
    assert (entry.target_kind, entry.target_id, entry.target_name) == ("user", op.pk, "op")
    assert entry.address == "192.0.2.8"


def test_a_sign_out_is_recorded(client, op):
    client.force_login(op)
    client.post(reverse("console:logout"))

    entry = AuditEntry.objects.get(action="session.sign_out")
    assert entry.actor == op
    assert entry.target_id == op.pk


def test_a_sign_out_while_signed_out_records_nothing(client):
    client.post(reverse("console:logout"))

    assert not AuditEntry.objects.exists()


def test_a_wrong_password_records_a_failed_sign_in_with_no_actor(client, op):
    _sign_in(client, password="wrong")

    entry = AuditEntry.objects.get()
    assert entry.action == "session.sign_in_failed"
    assert (entry.actor, entry.actor_name) == (None, "")
    assert (entry.target_kind, entry.target_id, entry.target_name) == ("user", None, "op")
    assert entry.address == "192.0.2.8"
    assert "wrong" not in str(entry.changes)


def test_an_unknown_username_records_what_was_typed(client):
    _sign_in(client, username="mallory")

    entry = AuditEntry.objects.get()
    assert (entry.action, entry.target_name) == ("session.sign_in_failed", "mallory")


def test_a_non_superuser_with_the_right_password_records_a_failed_sign_in(client):
    get_user_model().objects.create_user("op", password=PASSWORD, is_staff=True)

    _sign_in(client)

    entry = AuditEntry.objects.get()
    assert (entry.action, entry.actor, entry.target_name) == ("session.sign_in_failed", None, "op")


def test_a_failed_authenticate_outside_a_request_records_nothing(op):
    assert authenticate(None, username="op", password="wrong") is None

    assert not AuditEntry.objects.exists()


def test_the_receivers_connect_once(client, op):
    apps.get_app_config("console").ready()

    _sign_in(client)

    assert AuditEntry.objects.count() == 1
