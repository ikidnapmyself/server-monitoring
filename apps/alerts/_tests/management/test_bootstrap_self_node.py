from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.alerts.models import Node


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-1")
def test_bootstrap_creates_self_node():
    out = StringIO()
    call_command("bootstrap_self_node", stdout=out)
    assert Node.objects.filter(instance_id="hub-1", is_self=True).exists()
    assert "Self-node ready: hub-1" in out.getvalue()


@pytest.mark.django_db
@override_settings(INSTANCE_ID="")
def test_bootstrap_warns_when_unset():
    out = StringIO()
    call_command("bootstrap_self_node", stdout=out)
    assert Node.objects.filter(is_self=True).count() == 0
    assert "INSTANCE_ID is not set" in out.getvalue()
