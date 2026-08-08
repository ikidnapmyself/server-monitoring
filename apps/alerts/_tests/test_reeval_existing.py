import json

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.alerts.reeval_existing import (
    _metrics_of,
    apply_node_alert_reeval,
    preview_node_alert_reeval,
)


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

    def test_incident_not_resolved_when_an_alert_still_firing(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 99}})
        resolving = self._alert(node, "cpu", 85)  # -> warning, still firing
        inc = Incident.objects.create(title="t", severity="critical", status="open")
        inc.alerts.add(resolving)
        apply_node_alert_reeval(node)
        inc.refresh_from_db()
        self.assertEqual(inc.status, "open")

    def test_apply_is_idempotent(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._alert(node, "cpu", 95.2)
        apply_node_alert_reeval(node)
        second = apply_node_alert_reeval(node)
        self.assertEqual(second.changes, [])

    def test_metrics_of_none_and_malformed(self):
        node = self._node({})
        no_metrics = self._alert(node, "cpu", annotations={})
        malformed = self._alert(node, "cpu", annotations={"metrics": "not json"})
        not_dict = self._alert(node, "cpu", annotations={"metrics": "[1, 2]"})
        self.assertIsNone(_metrics_of(no_metrics))
        self.assertIsNone(_metrics_of(malformed))
        self.assertIsNone(_metrics_of(not_dict))
