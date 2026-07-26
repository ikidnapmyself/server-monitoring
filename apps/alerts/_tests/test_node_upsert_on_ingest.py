from django.test import TestCase

from apps.alerts.models import Node
from apps.alerts.services import AlertOrchestrator


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
