from django.test import TestCase

from apps.alerts.models import Node


class NodeModelTests(TestCase):
    def test_create_and_str(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03.example.com")
        self.assertEqual(str(node), "web-03 (web-03.example.com)")
        self.assertIsNotNone(node.first_seen)
        self.assertIsNotNone(node.last_seen)

    def test_instance_id_is_unique(self):
        Node.objects.create(instance_id="web-03")
        with self.assertRaises(Exception):
            Node.objects.create(instance_id="web-03")

    def test_upsert_creates_then_updates(self):
        n1 = Node.upsert(instance_id="web-03", hostname="h1", source="cluster")
        first_seen = n1.first_seen
        old_last = n1.last_seen

        n2 = Node.upsert(instance_id="web-03", hostname="h2", source="cluster")
        self.assertEqual(n1.pk, n2.pk)  # same row
        self.assertEqual(Node.objects.count(), 1)
        self.assertEqual(n2.hostname, "h2")  # updated
        self.assertEqual(n2.first_seen, first_seen)  # preserved
        self.assertGreaterEqual(n2.last_seen, old_last)

    def test_str_without_hostname(self):
        node = Node.objects.create(instance_id="web-03")
        self.assertEqual(str(node), "web-03")

    def test_node_config_defaults_to_empty_dict(self):
        node = Node.objects.create(instance_id="web-03")
        self.assertEqual(node.config, {})

    def test_node_config_stores_per_checker_thresholds(self):
        node = Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        node.refresh_from_db()
        self.assertEqual(node.config["cpu"]["critical_threshold"], 99)

    def test_upsert_without_hostname_sets_address_and_labels(self):
        node = Node.upsert(
            instance_id="web-03",
            address="10.0.0.3",
            source="cluster",
            labels={"role": "web"},
        )
        self.assertEqual(node.hostname, "")
        self.assertEqual(node.address, "10.0.0.3")
        self.assertEqual(node.labels, {"role": "web"})
        self.assertEqual(node.last_source, "cluster")
