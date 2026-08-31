from django.contrib import admin
from django.test import TestCase
from django.utils import timezone

from apps.alerts.admin import NodeAdmin
from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, AlertSeverity, Incident, IncidentStatus, Node
from apps.alerts.node_overview import build_identity, render_severity_chips, unresolved_counts
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


class SeverityChipTests(TestCase):
    def _incident(self, node, severity, status=IncidentStatus.OPEN):
        incident = Incident.objects.create(title="disk full", severity=severity, status=status)
        Alert.objects.create(
            fingerprint=f"f-{incident.pk}",
            source="cluster",
            name="disk",
            severity=severity,
            started_at=timezone.now(),
            node=node,
            incident=incident,
        )
        return incident

    def test_counts_unresolved_incidents_once_per_incident(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        incident = self._incident(node, AlertSeverity.CRITICAL)
        # A second alert on the SAME incident must not double the count.
        Alert.objects.create(
            fingerprint="f-second",
            source="cluster",
            name="cpu",
            severity=AlertSeverity.CRITICAL,
            started_at=timezone.now(),
            node=node,
            incident=incident,
        )
        counts = unresolved_counts(node)
        self.assertEqual(counts[AlertSeverity.CRITICAL], 1)

    def test_resolved_incidents_are_not_counted(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, AlertSeverity.CRITICAL, status=IncidentStatus.RESOLVED)
        self.assertEqual(unresolved_counts(node)[AlertSeverity.CRITICAL], 0)

    def test_a_quiet_node_renders_a_dash_not_a_zero(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(render_severity_chips(node), "—")

    def test_each_chip_links_to_that_severity_on_the_changelist(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, AlertSeverity.WARNING)
        html = render_severity_chips(node)
        self.assertIn(f"alerts__node__id__exact={node.pk}", html)
        self.assertIn("severity__exact=warning", html)
        self.assertIn("1 WARNING", html)

    def test_annotated_counts_are_reused_when_present(self):
        # The changelist annotates; the helper must not re-query in that case.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, AlertSeverity.CRITICAL)
        annotated = NodeAdmin(Node, admin.site).get_queryset(None).get(pk=node.pk)
        with self.assertNumQueries(0):
            counts = unresolved_counts(annotated)
        self.assertEqual(counts[AlertSeverity.CRITICAL], 1)
