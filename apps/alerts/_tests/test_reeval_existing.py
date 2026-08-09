import json

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.alerts.reeval_existing import (
    apply_node_alert_reeval,
    preview_node_alert_reeval,
)
from apps.alerts.reevaluation import parse_metrics


class ReevalExistingTests(TestCase):
    def _node(self, cfg):
        return Node.objects.create(instance_id="web-03", config=cfg)

    def _alert(
        self,
        node,
        checker,
        value=None,
        severity="critical",
        status="firing",
        metric="cpu_percent",
        annotations=None,
    ):
        if annotations is None:
            annotations = {"metrics": json.dumps({metric: value})}
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03",
            source="cluster",
            name=f"{checker} high",
            severity=severity,
            status=status,
            started_at=timezone.now(),
            node=node,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations=annotations,
        )

    def test_preview_reports_resolution_without_writing(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 95.2)
        report = preview_node_alert_reeval(node)
        self.assertEqual(len(report.changes), 1)
        self.assertEqual(report.changes[0].new_status, "resolved")
        a.refresh_from_db()
        self.assertEqual(a.status, "firing")  # preview did NOT write
        self.assertFalse(AlertHistory.objects.filter(alert=a).exists())

    def test_apply_resolves_and_audits(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 95.2)
        report = apply_node_alert_reeval(node)
        self.assertEqual(report.resolved_count, 1)
        self.assertEqual(report.severity_changed_count, 0)
        a.refresh_from_db()
        self.assertEqual(a.status, "resolved")
        self.assertEqual(a.severity, "info")
        self.assertIsNotNone(a.ended_at)
        self.assertIn("reevaluated_on_config_change", a.annotations)
        self.assertNotIn("severity_reevaluated", a.annotations)  # distinct key
        audit = json.loads(a.annotations["reevaluated_on_config_change"])
        self.assertEqual(audit["from"], "critical")
        self.assertEqual(audit["to"], "info")
        self.assertEqual(audit["status_from"], "firing")
        self.assertEqual(audit["status_to"], "resolved")
        self.assertEqual(audit["value"], 95.2)
        self.assertEqual(audit["thresholds"], {"warning_threshold": 99, "critical_threshold": 99})
        self.assertEqual(audit["checker"], "cpu")
        self.assertEqual(audit["by"], "hub-node-policy:config-change")
        self.assertIn("at", audit)
        history = AlertHistory.objects.get(alert=a)
        self.assertEqual(history.event, "resolved")
        self.assertEqual(history.old_status, "firing")
        self.assertEqual(history.new_status, "resolved")

    def test_apply_changes_severity_when_still_firing(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 85)  # was critical, now warning (>=80, <99)
        report = apply_node_alert_reeval(node)
        self.assertEqual(report.resolved_count, 0)
        self.assertEqual(report.severity_changed_count, 1)
        a.refresh_from_db()
        self.assertEqual(a.severity, "warning")
        self.assertEqual(a.status, "firing")
        self.assertIsNone(a.ended_at)
        history = AlertHistory.objects.get(alert=a)
        self.assertEqual(history.event, "reevaluated")
        self.assertEqual(history.new_status, "firing")

    def test_skips_when_no_config(self):
        node = self._node({})  # no config
        self._alert(node, "cpu", 95.2)
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_skips_when_no_metrics_annotation(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._alert(node, "cpu", annotations={})
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_skips_when_unchanged(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 90}})
        self._alert(node, "cpu", 95)  # already critical + firing -> no change
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_listening_ports_resolves_when_allowlist_covers(self):
        node = self._node({"listening_ports": {"allowlist": [22, 80]}})
        a = self._alert(
            node,
            "listening_ports",
            severity="warning",
            annotations={
                "metrics": json.dumps(
                    {"listening": [{"port": 22, "exposed": True}, {"port": 80, "exposed": True}]}
                )
            },
        )
        report = apply_node_alert_reeval(node)
        self.assertEqual(report.resolved_count, 1)
        a.refresh_from_db()
        self.assertEqual(a.status, "resolved")
        self.assertEqual(a.severity, "info")
        audit = json.loads(a.annotations["reevaluated_on_config_change"])
        self.assertEqual(audit["checker"], "listening_ports")
        self.assertEqual(audit["value"], 0.0)
        self.assertEqual(audit["thresholds"], {"allowlist": [22, 80]})

    def test_listening_ports_still_flagged_no_change(self):
        node = self._node({"listening_ports": {"allowlist": [22]}})
        self._alert(
            node,
            "listening_ports",
            severity="warning",
            annotations={"metrics": json.dumps({"listening": [{"port": 9999, "exposed": True}]})},
        )
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_skips_non_numeric_checker(self):
        node = self._node({"raid": {"warning_threshold": 1, "critical_threshold": 2}})
        self._alert(node, "raid", 1, metric="array_count")
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_skips_non_firing_alert(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._alert(node, "cpu", 95.2, severity="info", status="resolved")
        self.assertEqual(preview_node_alert_reeval(node).changes, [])

    def test_incident_auto_resolves_when_last_alert_resolves(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 95.2)
        inc = Incident.objects.create(title="t", severity="critical", status="open")
        inc.alerts.add(a)
        apply_node_alert_reeval(node)
        inc.refresh_from_db()
        self.assertEqual(inc.status, "resolved")

    def test_incident_with_still_firing_alert_stays_open_during_sweep(self):
        # The sweep runs (a cpu alert resolves), but an incident that still holds
        # a firing alert (memory, no config -> not re-scored) must stay open.
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._alert(node, "cpu", 95.2)  # -> resolved (drives resolved_count > 0)
        still_firing = self._alert(node, "memory", 50, metric="memory_percent")  # stays firing
        inc = Incident.objects.create(title="t", severity="critical", status="open")
        inc.alerts.add(still_firing)
        apply_node_alert_reeval(node)
        inc.refresh_from_db()
        self.assertEqual(inc.status, "open")

    def test_apply_is_idempotent(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._alert(node, "cpu", 95.2)
        apply_node_alert_reeval(node)
        second = apply_node_alert_reeval(node)
        self.assertEqual(second.changes, [])

    def test_metrics_parse_none_and_malformed(self):
        self.assertIsNone(parse_metrics({}))
        self.assertIsNone(parse_metrics({"metrics": "not json"}))
        self.assertIsNone(parse_metrics({"metrics": "[1, 2]"}))

    def test_severity_only_change_does_not_resolve_incident(self):
        # A run that only changes severity (no resolutions) must not sweep
        # incidents — a pre-existing open incident whose alerts are all
        # non-firing stays open.
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 85)  # critical -> warning, still firing
        # An unrelated open incident on this node whose alert is already resolved.
        other = self._alert(
            node, "memory", 10, severity="info", status="resolved", metric="memory_percent"
        )
        inc = Incident.objects.create(title="stale", severity="info", status="open")
        inc.alerts.add(other)

        report = apply_node_alert_reeval(node)

        self.assertEqual(report.resolved_count, 0)
        self.assertEqual(report.severity_changed_count, 1)
        a.refresh_from_db()
        self.assertEqual(a.severity, "warning")
        inc.refresh_from_db()
        self.assertEqual(inc.status, "open")  # NOT auto-resolved

    def test_history_details_carry_severity_delta(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 85)  # critical -> warning
        apply_node_alert_reeval(node)
        history = AlertHistory.objects.get(alert=a, event="reevaluated")
        self.assertEqual(history.details["severity_from"], "critical")
        self.assertEqual(history.details["severity_to"], "warning")
