import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.alerts.models import Node


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-1")
def test_bootstrap_creates_self_node():
    call_command("bootstrap_self_node")
    assert Node.objects.filter(instance_id="hub-1", is_self=True).exists()


@pytest.mark.django_db
@override_settings(INSTANCE_ID="")
def test_bootstrap_warns_when_unset():
    call_command("bootstrap_self_node")
    assert Node.objects.filter(is_self=True).count() == 0
