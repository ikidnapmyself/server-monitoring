from django.test import TestCase

from apps.alerts.models import Node
from apps.alerts.services import AlertOrchestrator, register_pushing_node


class NodeUpsertOnIngestTests(TestCase):
    def test_cluster_push_upserts_node(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-03",
            "hostname": "web-03.example.com",
            "alerts": [],
        }
        AlertOrchestrator().process_webhook(payload, driver="cluster")
        node = Node.objects.get(instance_id="web-03")
        self.assertEqual(node.hostname, "web-03.example.com")
        self.assertEqual(node.last_source, "cluster")

    def test_non_cluster_push_creates_no_node(self):
        AlertOrchestrator().process_webhook({"name": "x", "status": "firing"}, driver="generic")
        self.assertEqual(Node.objects.count(), 0)

    def test_cluster_push_without_instance_id_creates_no_node(self):
        AlertOrchestrator().process_webhook({"source": "cluster", "alerts": []}, driver="cluster")
        self.assertEqual(Node.objects.count(), 0)


class RegisterPushingNodeSourceTests(TestCase):
    """``last_source`` records HOW the row was last touched, so it must be true.

    ``cluster`` means the row arrived by push from another machine; ``local``
    means a check run on this machine registered it. ``push_to_hub --local``
    never leaves the machine, so it is the second.
    """

    PAYLOAD = {"source": "cluster", "instance_id": "hub-1", "hostname": "hub-1.local"}

    def test_defaults_to_cluster_for_the_webhook_path(self):
        node = register_pushing_node(dict(self.PAYLOAD))
        self.assertEqual(node.last_source, "cluster")

    def test_caller_can_record_a_local_registration(self):
        node = register_pushing_node(dict(self.PAYLOAD), source="local")
        self.assertEqual(node.last_source, "local")
