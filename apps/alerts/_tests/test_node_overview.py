from django.contrib import admin
from django.test import TestCase
from django.utils import timezone

from apps.alerts.admin import NodeAdmin
from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, AlertSeverity, Incident, IncidentStatus, Node
from apps.alerts.node_overview import (
    SEVERITY_COLORS,
    build_checker_rows,
    build_identity,
    build_incident_rows,
    render_severity_chips,
    unresolved_counts,
)
from apps.checkers.models import CheckRun
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

    def test_the_local_node_stale_past_the_window_still_reads_informational(self):
        # config/dashboard.py keeps this instance out of the freshness verdict: its
        # last_seen only says somebody ran a check here, so amber would be permanent.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        Node.objects.filter(pk=node.pk).update(
            last_seen=timezone.now() - timezone.timedelta(minutes=NODE_RECENT_MINUTES + 1)
        )
        node.refresh_from_db()
        self.assertEqual(build_identity(node).freshness_status, "info")

    def test_the_local_node_inside_the_window_also_reads_informational(self):
        # The window does not apply to us at all, in either direction.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self.assertEqual(build_identity(node).freshness_status, "info")

    def test_the_local_node_label_names_it_as_a_self_check(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self.assertIn("self-check", build_identity(node).freshness_label)


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


class CheckerStateTests(TestCase):
    def test_local_node_reads_its_own_check_runs_newest_first_per_checker(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        CheckRun.objects.create(
            checker_name="disk",
            hostname="hub",
            status="ok",
            metrics={"worst_percent": 40.0},
            executed_at=timezone.now() - timezone.timedelta(minutes=10),
        )
        CheckRun.objects.create(
            checker_name="disk",
            hostname="hub",
            status="critical",
            metrics={"worst_percent": 91.0},
            executed_at=timezone.now(),
        )
        rows = build_checker_rows(node)
        self.assertEqual([r.checker for r in rows], ["disk"])
        self.assertEqual(rows[0].status, "critical")
        self.assertIn("91", rows[0].value)

    def test_a_peer_reads_its_alert_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="check:web-03:cpu",
            source="cluster",
            name="cpu",
            severity=AlertSeverity.WARNING,
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu"},
            annotations={"cpu_percent": "93.5"},
        )
        rows = build_checker_rows(node)
        self.assertEqual(rows[0].checker, "cpu")
        self.assertIn("93.5", rows[0].value)
        self.assertEqual(rows[0].status, AlertSeverity.WARNING)

    def test_a_peer_alert_with_no_checker_label_is_skipped(self):
        # Webhook alerts are not checker results and have no place in this table.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="grafana-1",
            source="grafana",
            name="latency",
            severity=AlertSeverity.WARNING,
            started_at=timezone.now(),
            node=node,
        )
        self.assertEqual(build_checker_rows(node), [])

    def test_a_checker_with_no_known_primary_metric_still_renders(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="check:web-03:raid",
            source="cluster",
            name="raid",
            severity=AlertSeverity.INFO,
            started_at=timezone.now(),
            node=node,
            labels={"checker": "raid"},
            annotations={},
        )
        rows = build_checker_rows(node)
        self.assertEqual(rows[0].checker, "raid")
        self.assertEqual(rows[0].value, "\u2014")

    def test_a_node_that_reported_nothing_yields_no_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_checker_rows(node), [])

    def test_rows_are_sorted_by_checker_name(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        for name in ("memory", "cpu", "disk"):
            Alert.objects.create(
                fingerprint=f"check:web-03:{name}",
                source="cluster",
                name=name,
                severity=AlertSeverity.INFO,
                started_at=timezone.now(),
                node=node,
                labels={"checker": name},
                annotations={},
            )
        self.assertEqual([r.checker for r in build_checker_rows(node)], ["cpu", "disk", "memory"])

    def test_a_metric_that_is_not_a_number_is_shown_as_it_arrived(self):
        # Not every metric is numeric, and a checker is free to report a word.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="check:web-03:cpu",
            source="cluster",
            name="cpu",
            severity=AlertSeverity.INFO,
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu"},
            annotations={"cpu_percent": "unavailable"},
        )
        self.assertEqual(build_checker_rows(node)[0].value, "unavailable")

    def test_only_the_newest_alert_per_checker_is_kept(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        for suffix in ("a", "b"):
            Alert.objects.create(
                fingerprint=f"check:web-03:cpu-{suffix}",
                source="cluster",
                name="cpu",
                severity=AlertSeverity.INFO,
                started_at=timezone.now(),
                node=node,
                labels={"checker": "cpu"},
                annotations={"cpu_percent": "10"},
            )
        self.assertEqual(len(build_checker_rows(node)), 1)

    def test_a_local_checker_with_no_primary_metric_reads_as_a_dash(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        CheckRun.objects.create(
            checker_name="raid",
            hostname="hub",
            status="ok",
            metrics={},
            executed_at=timezone.now(),
        )
        rows = build_checker_rows(node)
        self.assertEqual(rows[0].checker, "raid")
        self.assertEqual(rows[0].value, "—")


class RecentIncidentTests(TestCase):
    def _incident(self, node, title, severity=AlertSeverity.WARNING, created_at=None):
        incident = Incident.objects.create(title=title, severity=severity)
        if created_at is not None:
            # created_at is auto_now_add, and two rows written in the same test can
            # land on the same timestamp, so ordering is stamped explicitly here.
            Incident.objects.filter(pk=incident.pk).update(created_at=created_at)
        Alert.objects.create(
            fingerprint=f"f-{title}",
            source="cluster",
            name=title,
            severity=severity,
            started_at=timezone.now(),
            node=node,
            incident=incident,
        )
        return incident

    def test_lists_the_nodes_incidents_newest_first(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        now = timezone.now()
        self._incident(node, "older", created_at=now - timezone.timedelta(minutes=5))
        self._incident(node, "newer", created_at=now)
        rows = build_incident_rows(node)
        self.assertEqual([r.title for r in rows], ["newer", "older"])

    def test_counts_an_incident_once_however_many_alerts_reached_it(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        incident = self._incident(node, "disk full")
        Alert.objects.create(
            fingerprint="f-second",
            source="cluster",
            name="disk",
            severity=AlertSeverity.WARNING,
            started_at=timezone.now(),
            node=node,
            incident=incident,
        )
        self.assertEqual(len(build_incident_rows(node)), 1)

    def test_caps_at_ten(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        for i in range(12):
            self._incident(node, f"i{i}")
        self.assertEqual(len(build_incident_rows(node)), 10)

    def test_another_nodes_incidents_are_not_listed(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        other = Node.objects.create(instance_id="web-04", hostname="web-04")
        self._incident(other, "theirs")
        self.assertEqual(build_incident_rows(node), [])

    def test_a_node_with_no_incidents_yields_no_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_incident_rows(node), [])

    def test_each_row_links_to_the_incident_and_carries_its_severity_color(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        incident = self._incident(node, "hot disk", severity=AlertSeverity.CRITICAL)
        row = build_incident_rows(node)[0]
        self.assertEqual(row.severity, AlertSeverity.CRITICAL)
        self.assertEqual(row.status, IncidentStatus.OPEN)
        self.assertEqual(row.color, SEVERITY_COLORS[AlertSeverity.CRITICAL])
        self.assertIn(str(incident.pk), row.url)

    def test_an_unknown_severity_falls_back_to_the_neutral_color(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._incident(node, "odd", severity="mauve")
        self.assertEqual(build_incident_rows(node)[0].color, "#6c757d")
