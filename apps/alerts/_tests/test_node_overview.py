from django.test import TestCase
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Node
from apps.alerts.node_overview import build_identity
from config.dashboard import NODE_RECENT_MINUTES


class IdentityHeaderTests(TestCase):
    def test_the_local_node_is_named_as_this_hub(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        identity = build_identity(node)
        self.assertTrue(identity.is_local)
        self.assertEqual(identity.role_label, "This hub")

    def test_any_other_node_is_a_peer(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        identity = build_identity(node)
        self.assertFalse(identity.is_local)
        self.assertEqual(identity.role_label, "Peer")

    def test_a_node_seen_just_now_reads_green(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_identity(node).freshness_status, "ok")

    def test_a_node_quiet_past_the_dashboard_window_reads_amber(self):
        # Same threshold the dashboard nodes card uses, so the two never disagree.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Node.objects.filter(pk=node.pk).update(
            last_seen=timezone.now() - timezone.timedelta(minutes=NODE_RECENT_MINUTES + 1)
        )
        node.refresh_from_db()
        self.assertEqual(build_identity(node).freshness_status, "warn")

    def test_the_freshness_label_carries_an_age(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertIn("ago", build_identity(node).freshness_label)
