import pytest
from django.test import override_settings

from apps.alerts.models import Node


@pytest.mark.django_db
def test_node_is_self_defaults_false():
    node = Node.objects.create(instance_id="agent-1")
    assert node.is_self is False


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-xyz")
def test_ensure_self_creates_self_node():
    node = Node.ensure_self()
    assert node is not None
    assert node.instance_id == "hub-xyz"
    assert node.is_self is True


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-xyz")
def test_ensure_self_is_idempotent():
    first = Node.ensure_self()
    second = Node.ensure_self()
    assert first.pk == second.pk
    assert Node.objects.filter(is_self=True).count() == 1


@pytest.mark.django_db
@override_settings(INSTANCE_ID="")
def test_ensure_self_noop_when_instance_id_unset():
    assert Node.ensure_self() is None
    assert Node.objects.filter(is_self=True).count() == 0
