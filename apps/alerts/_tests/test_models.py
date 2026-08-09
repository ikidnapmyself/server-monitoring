import pytest

from apps.alerts.models import Node


@pytest.mark.django_db
def test_node_is_self_defaults_false():
    node = Node.objects.create(instance_id="agent-1")
    assert node.is_self is False
