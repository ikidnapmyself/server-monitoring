from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.alerts.admin import NodeAdmin
from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, AlertSeverity, Incident, IncidentStatus, Node
from apps.alerts.node_overview import (
    SEVERITY_COLORS,
    build_charts,
    build_checker_rows,
    build_identity,
    build_incident_rows,
    build_pipeline_rows,
    build_preflight,
    charts_note,
    render_severity_chips,
    unresolved_counts,
)
from apps.checkers.models import CheckRun, PreflightRun
from apps.orchestration.models import PipelineRun
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
    def _run(self, checker, status, metrics, executed_at=None):
        run = CheckRun.objects.create(
            checker_name=checker,
            hostname="hub",
            status=status,
            metrics=metrics,
        )
        if executed_at is not None:
            # executed_at is auto_now_add, so a value passed to create() is discarded;
            # stamping it afterwards is what actually spaces the rows out in time.
            CheckRun.objects.filter(pk=run.pk).update(executed_at=executed_at)
        return run

    def test_local_node_reads_its_own_check_runs_newest_first_per_checker(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        now = timezone.now()
        # Written newest first, so this passes only if the order comes from
        # executed_at and not from the order the rows were inserted in.
        self._run("disk", "critical", {"worst_percent": 91.0}, executed_at=now)
        self._run(
            "disk",
            "ok",
            {"worst_percent": 40.0},
            executed_at=now - timezone.timedelta(minutes=10),
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

    def test_the_local_scan_is_one_query_per_checker_not_a_table_walk(self):
        # There is no CheckRun retention, so a table walk grows without bound:
        # a host checking every 5 minutes writes ~4k rows a day. The cost must
        # track the number of distinct checkers, not the number of rows.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        for _ in range(10):
            for name in ("cpu", "disk", "memory"):
                self._run(name, "ok", {}, executed_at=timezone.now())
        # one query to discover the names, then one newest-row query per name
        with self.assertNumQueries(4):
            rows = build_checker_rows(node)
        self.assertEqual([r.checker for r in rows], ["cpu", "disk", "memory"])

    def test_a_checker_absent_from_the_metric_map_is_still_listed(self):
        # The names come from the data, not from a static list: a checker that
        # ran and is in no map is exactly the row an operator needs to see.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("listening_ports", "ok", {})
        self.assertEqual([r.checker for r in build_checker_rows(node)], ["listening_ports"])

    def test_the_peer_scan_reads_only_checker_alerts(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        for index in range(10):
            Alert.objects.create(
                fingerprint=f"grafana-{index}",
                source="grafana",
                name="latency",
                severity=AlertSeverity.WARNING,
                started_at=timezone.now(),
                node=node,
            )
        with self.assertNumQueries(1):
            self.assertEqual(build_checker_rows(node), [])

    def test_a_peer_alert_with_a_blank_checker_label_is_skipped(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="blank-checker",
            source="cluster",
            name="cpu",
            severity=AlertSeverity.INFO,
            started_at=timezone.now(),
            node=node,
            labels={"checker": ""},
        )
        self.assertEqual(build_checker_rows(node), [])

    def test_a_peer_alert_with_non_dict_labels_does_not_crash_the_page(self):
        # labels arrives over a webhook and is attacker-controlled: it can be a
        # string. Same defence as services.instance_key_from_labels.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="hostile-labels",
            source="grafana",
            name="cpu",
            severity=AlertSeverity.INFO,
            started_at=timezone.now(),
            node=node,
            labels="not-a-dict",
        )
        self.assertEqual(build_checker_rows(node), [])

    def test_a_peer_alert_with_non_dict_annotations_still_renders(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        Alert.objects.create(
            fingerprint="check:web-03:cpu",
            source="cluster",
            name="cpu",
            severity=AlertSeverity.INFO,
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu"},
            annotations="nope",
        )
        rows = build_checker_rows(node)
        self.assertEqual(rows[0].checker, "cpu")
        self.assertEqual(rows[0].value, "\u2014")

    def test_a_check_run_with_non_dict_metrics_still_renders(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("cpu", "ok", "nope")
        rows = build_checker_rows(node)
        self.assertEqual(rows[0].checker, "cpu")
        self.assertEqual(rows[0].value, "\u2014")

    def test_a_boolean_metric_reads_as_a_dash_not_as_one_point_zero(self):
        # build_charts refuses to plot a bool; the state table must agree.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("cpu", "ok", {"cpu_percent": True})
        self.assertEqual(build_checker_rows(node)[0].value, "\u2014")

    def test_a_local_checker_with_no_primary_metric_reads_as_a_dash(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("raid", "ok", {})
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


class ChartTests(TestCase):
    def _run(self, checker, metric, value, minutes_ago=0, alert=None):
        run = CheckRun.objects.create(
            checker_name=checker,
            hostname="hub",
            status="ok",
            metrics={metric: value},
            alert=alert,
        )
        # executed_at is auto_now_add, so a value passed to create() is discarded;
        # stamping it afterwards is what actually spaces the rows out in time.
        CheckRun.objects.filter(pk=run.pk).update(
            executed_at=timezone.now() - timezone.timedelta(minutes=minutes_ago)
        )
        run.refresh_from_db()
        return run

    def test_the_local_node_gets_disk_cpu_and_memory(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("disk", "worst_percent", 40.0)
        self._run("cpu", "cpu_percent", 12.0)
        self._run("memory", "memory_percent", 55.0)
        charts = build_charts(node)
        self.assertEqual([c.title for c in charts], ["Disk usage", "CPU", "Memory"])
        self.assertIn("<svg", charts[0].svg)

    def test_a_checker_with_no_history_is_omitted_rather_than_drawn_empty(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("disk", "worst_percent", 40.0)
        self.assertEqual([c.title for c in build_charts(node)], ["Disk usage"])

    def test_runs_with_a_non_numeric_metric_are_skipped(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("disk", "worst_percent", "n/a", minutes_ago=2)
        self._run("disk", "worst_percent", 40.0, minutes_ago=1)
        charts = build_charts(node)
        self.assertEqual(len(charts), 1)

    def test_a_boolean_metric_is_not_plotted_as_one(self):
        # bool subclasses int, so True would sneak through a bare isinstance check
        # and draw a 1.0 that no checker ever measured.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("disk", "worst_percent", True)
        self.assertEqual(build_charts(node), [])

    def test_the_latest_value_is_formatted_like_the_state_table(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self._run("cpu", "cpu_percent", 12)
        self.assertEqual(build_charts(node)[0].latest, "12.0")

    def test_a_run_that_raised_an_alert_is_marked_on_the_line(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        alert = Alert.objects.create(
            fingerprint="check:hub:disk",
            name="disk",
            severity=AlertSeverity.CRITICAL,
            source="cluster",
            started_at=timezone.now(),
        )
        self._run("disk", "worst_percent", 40.0, minutes_ago=1)
        self._run("disk", "worst_percent", 95.0, alert=alert)
        svg = build_charts(node)[0].svg
        self.assertIn('fill="#d33"', svg)

    def test_a_peer_gets_no_charts(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_charts(node), [])

    def test_a_peer_is_told_why_rather_than_shown_a_blank_chart(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertIn("not pushed to a hub", charts_note(node))

    def test_the_local_node_needs_no_note(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self.assertEqual(charts_note(node), "")


class PreflightPanelTests(TestCase):
    def test_the_local_node_shows_its_latest_run(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        PreflightRun.objects.create(
            instance_id=local_instance_id(),
            overall_status="warn",
            passed=9,
            warnings=2,
            errors=0,
        )
        panel = build_preflight(node)
        self.assertEqual(panel.run.overall_status, "warn")
        self.assertEqual(panel.note, "")

    def test_the_newest_run_wins(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        old = PreflightRun.objects.create(instance_id=local_instance_id(), overall_status="ok")
        PreflightRun.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=5)
        )
        PreflightRun.objects.create(instance_id=local_instance_id(), overall_status="error")
        self.assertEqual(build_preflight(node).run.overall_status, "error")

    @override_settings(INSTANCE_ID="")
    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    def test_the_regression_a_hub_with_no_instance_id_env_var(self, mock_log, mock_read):
        # This is the bug that started the whole change: the hub's node row is
        # keyed by the hostname fallback, and the run must match it.
        mock_read.return_value = None
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        self.assertNotEqual(node.instance_id, "")  # the fallback really is in play
        call_command("preflight", "--json", stdout=StringIO())
        self.assertIsNotNone(build_preflight(node).run)

    def test_the_local_node_with_no_run_yet_says_so(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        panel = build_preflight(node)
        self.assertIsNone(panel.run)
        self.assertIn("No preflight recorded", panel.note)

    def test_a_peer_is_told_preflight_is_node_local(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        panel = build_preflight(node)
        self.assertIsNone(panel.run)
        self.assertIn("node-local", panel.note)

    def test_a_peers_own_preflight_row_is_still_not_shown(self):
        # Even if a row somehow carries a peer's instance_id, the panel is local-only.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        PreflightRun.objects.create(instance_id="web-03", overall_status="ok")
        self.assertIsNone(build_preflight(node).run)


class PipelinePanelTests(TestCase):
    def test_lists_the_ten_newest_runs_with_links(self):
        node = Node.objects.create(instance_id="web-06", hostname="web-06")
        old = PipelineRun.objects.create(trace_id="t1", run_id="run-old", node=node)
        new = PipelineRun.objects.create(trace_id="t2", run_id="run-new", node=node)
        # created_at is auto_now_add, so ordering is stamped explicitly here.
        PipelineRun.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=5)
        )
        PipelineRun.objects.filter(pk=new.pk).update(created_at=timezone.now())
        rows = build_pipeline_rows(node)
        self.assertEqual([r.run_id for r in rows], ["run-new", "run-old"])
        self.assertIn(f"/admin/orchestration/pipelinerun/{new.pk}/change/", rows[0].url)
        self.assertEqual(rows[0].origin, new.origin)
        self.assertEqual(rows[0].status, new.status)
        self.assertIsNotNone(rows[0].created_at)

    def test_caps_at_ten(self):
        node = Node.objects.create(instance_id="web-06", hostname="web-06")
        for i in range(12):
            PipelineRun.objects.create(trace_id="t", run_id=f"run-{i}", node=node)
        self.assertEqual(len(build_pipeline_rows(node)), 10)

    def test_another_nodes_runs_are_not_listed(self):
        node = Node.objects.create(instance_id="web-06", hostname="web-06")
        other = Node.objects.create(instance_id="web-07", hostname="web-07")
        PipelineRun.objects.create(trace_id="t", run_id="theirs", node=other)
        self.assertEqual(build_pipeline_rows(node), [])

    def test_a_node_with_no_runs_yields_no_rows(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(build_pipeline_rows(node), [])
