import json
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, IncidentStatus, Node
from apps.alerts.reeval_existing import (
    ReevalScope,
    apply_node_alert_reeval,
    apply_reeval,
    preview_node_alert_reeval,
    preview_reeval,
)
from apps.alerts.reevaluation import SkipReason, parse_metrics
from apps.alerts.services import announce_incident_change
from apps.orchestration.models import (
    PipelineOrigin,
    PipelineRun,
    PipelineStatus,
    StageExecution,
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

    def test_history_marks_the_re_evaluation(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._alert(node, "cpu", 95.2)
        apply_node_alert_reeval(node)
        history = AlertHistory.objects.get(alert=alert)
        self.assertEqual(history.event, "resolved")
        self.assertEqual(history.details["by"], "hub-node-policy:config-change")

    def test_history_marks_the_re_evaluation_on_a_severity_change(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 99}})
        alert = self._alert(node, "cpu", 85)
        apply_node_alert_reeval(node)
        history = AlertHistory.objects.get(alert=alert)
        self.assertEqual(history.event, "reevaluated")
        self.assertEqual(history.details["by"], "hub-node-policy:config-change")

    def test_finds_unlinked_alert_by_instance_id_label(self):
        # Alert created before the node registered → node FK is NULL, but its
        # instance_id label still identifies it. Reeval must find it by label.
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 95.2)
        a.node = None
        a.save(update_fields=["node"])
        report = preview_node_alert_reeval(node)
        self.assertEqual(len(report.changes), 1)
        self.assertEqual(report.changes[0].new_status, "resolved")

    def test_apply_resolves_unlinked_alert_and_incident(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        a = self._alert(node, "cpu", 95.2)
        a.node = None
        a.save(update_fields=["node"])
        inc = Incident.objects.create(title="t", severity="critical", status="open")
        inc.alerts.add(a)
        apply_node_alert_reeval(node)
        a.refresh_from_db()
        self.assertEqual(a.status, "resolved")
        inc.refresh_from_db()
        self.assertEqual(inc.status, "resolved")

    def test_ignores_other_nodes_alerts(self):
        # A firing alert for a DIFFERENT instance_id must not be swept in.
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        other = Alert.objects.create(
            fingerprint="cpu-web-99",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=None,
            labels={"checker": "cpu", "instance_id": "web-99"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.2})},
        )
        self.assertEqual(preview_node_alert_reeval(node).changes, [])
        other.refresh_from_db()
        self.assertEqual(other.status, "firing")

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


class ReevalScopeTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(instance_id="web-03", config={})

    def _alert(self, checker, status="firing"):
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03",
            source="cluster",
            name=f"{checker} high",
            severity="critical",
            status=status,
            started_at=timezone.now(),
            node=self.node,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.0})},
        )

    def test_node_scope_covers_every_firing_alert(self):
        self._alert("cpu")
        self._alert("memory")
        self._alert("disk", status="resolved")
        scope = ReevalScope.for_node(self.node)
        self.assertEqual(scope.alerts.count(), 2)

    def test_checker_scope_narrows_to_one_checker(self):
        self._alert("cpu")
        self._alert("memory")
        scope = ReevalScope.for_checker(self.node, "cpu")
        self.assertEqual([a.labels["checker"] for a in scope.alerts], ["cpu"])

    def test_alert_scope_is_exactly_one_alert(self):
        alert = self._alert("cpu")
        self._alert("memory")
        scope = ReevalScope.for_alert(alert)
        self.assertEqual([a.pk for a in scope.alerts], [alert.pk])
        self.assertEqual(scope.node, self.node)

    def test_alert_scope_finds_the_node_by_label_not_fk(self):
        alert = self._alert("cpu")
        alert.node = None
        alert.save(update_fields=["node"])
        scope = ReevalScope.for_alert(alert)
        self.assertEqual(scope.node, self.node)

    def test_the_checker_scopes_sum_to_the_node_scope(self):
        self._alert("cpu")
        self._alert("memory")
        node_pks = {a.pk for a in ReevalScope.for_node(self.node).alerts}
        summed = set()
        for checker in ("cpu", "memory"):
            summed |= {a.pk for a in ReevalScope.for_checker(self.node, checker).alerts}
        self.assertEqual(node_pks, summed)

    def test_node_scope_is_empty_when_nothing_is_firing(self):
        self._alert("cpu", status="resolved")
        self.assertEqual(list(ReevalScope.for_node(self.node).alerts), [])

    def test_checker_scope_is_empty_for_a_checker_with_no_alerts(self):
        self._alert("cpu")
        self.assertEqual(list(ReevalScope.for_checker(self.node, "memory").alerts), [])

    def test_alert_scope_has_no_node_when_the_label_names_an_unknown_node(self):
        alert = self._alert("cpu")
        alert.labels = {"checker": "cpu", "instance_id": "web-99"}
        alert.save(update_fields=["labels"])
        scope = ReevalScope.for_alert(alert)
        self.assertIsNone(scope.node)
        self.assertEqual([a.pk for a in scope.alerts], [alert.pk])

    def test_alert_scope_keeps_a_resolved_alert(self):
        alert = self._alert("cpu", status="resolved")
        self.assertEqual([a.pk for a in ReevalScope.for_alert(alert).alerts], [alert.pk])

    def test_node_scope_includes_an_alert_with_no_node_fk(self):
        alert = self._alert("cpu")
        alert.node = None
        alert.save(update_fields=["node"])
        self.assertEqual([a.pk for a in ReevalScope.for_node(self.node).alerts], [alert.pk])

    def test_node_scope_excludes_an_alert_whose_fk_points_at_this_node_but_label_does_not(self):
        alert = self._alert("cpu")
        alert.labels = {"checker": "cpu", "instance_id": "web-99"}
        alert.save(update_fields=["labels"])
        self.assertEqual(list(ReevalScope.for_node(self.node).alerts), [])

    def test_alert_scope_has_no_node_when_labels_are_empty(self):
        Node.objects.create(instance_id="", config={})
        alert = self._alert("cpu")
        alert.labels = {}
        alert.save(update_fields=["labels"])
        self.assertIsNone(ReevalScope.for_alert(alert).node)

    def test_only_the_checker_scope_records_a_checker(self):
        alert = self._alert("cpu")
        self.assertIsNone(ReevalScope.for_node(self.node).checker)
        self.assertIsNone(ReevalScope.for_alert(alert).checker)
        self.assertEqual(ReevalScope.for_checker(self.node, "cpu").checker, "cpu")


class ReportSkipTests(TestCase):
    def _node(self, cfg):
        return Node.objects.create(instance_id="web-03", config=cfg)

    def _alert(
        self,
        node,
        checker="cpu",
        value=95.0,
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

    def test_a_no_op_preview_explains_each_alert(self):
        node = self._node({})
        self._alert(node)
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual(report.changes, [])
        self.assertEqual(len(report.skips), 1)
        self.assertIn("No policy set for cpu on web-03.", report.skips[0].sentence)
        self.assertEqual(report.skips[0].reason, SkipReason.NO_POLICY)

    def test_an_unchanged_score_is_a_skip_not_a_silence(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 90}})
        self._alert(node, value=95.0)  # already critical + firing
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual(report.changes, [])
        self.assertEqual(report.skips[0].reason, SkipReason.UNCHANGED)
        self.assertEqual(
            report.skips[0].sentence,
            "Policy already matches: cpu is at 95.0, warning starts at 80.",
        )

    def test_a_checker_with_no_scorer_is_a_skip(self):
        node = self._node({})
        self._alert(node, checker="raid", annotations={})
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual(report.skips[0].reason, SkipReason.NO_SCORER)
        self.assertIn("not re-evaluatable", report.skips[0].sentence)

    def test_an_alert_with_no_readable_metrics_is_a_skip(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 90}})
        self._alert(node, annotations={})
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual(report.skips[0].reason, SkipReason.NO_METRICS)
        self.assertIn("no readable metrics", report.skips[0].sentence)

    def test_an_unchanged_allowlist_score_does_not_quote_a_threshold(self):
        node = self._node({"listening_ports": {"allowlist": [22]}})
        self._alert(
            node,
            checker="listening_ports",
            severity="warning",
            annotations={"metrics": json.dumps({"listening": [{"port": 9999, "exposed": True}]})},
        )
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual(report.changes, [])
        self.assertEqual(report.skips[0].reason, SkipReason.UNCHANGED_NO_THRESHOLD)
        self.assertEqual(
            report.skips[0].sentence,
            "Policy already matches: the listening_ports policy on web-03 "
            "scores this alert exactly as it stands.",
        )
        self.assertNotIn("None", report.skips[0].sentence)

    def test_a_mixed_scope_populates_both_lists(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        changing = self._alert(node, value=95.2)
        skipping = self._alert(node, checker="memory", value=50.0, metric="memory_percent")
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual([c.alert.pk for c in report.changes], [changing.pk])
        self.assertEqual([s.alert.pk for s in report.skips], [skipping.pk])
        self.assertEqual(report.skips[0].reason, SkipReason.NO_POLICY)

    def test_a_checker_scope_reports_only_that_checker(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        cpu = self._alert(node, value=95.2)
        self._alert(node, checker="memory", value=50.0, metric="memory_percent")
        report = preview_reeval(ReevalScope.for_checker(node, "cpu"))
        self.assertEqual([c.alert.pk for c in report.changes], [cpu.pk])
        self.assertEqual(report.skips, [])

    def test_an_alert_scope_re_fires_a_resolved_alert_the_policy_still_flags(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 90}})
        alert = self._alert(node, value=95.0, severity="info", status="resolved")
        alert.ended_at = timezone.now()
        alert.save(update_fields=["ended_at"])
        report = apply_reeval(ReevalScope.for_alert(alert))
        self.assertEqual(report.severity_changed_count, 1)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertIsNone(alert.ended_at)

    def test_the_node_wide_function_delegates_to_the_node_scope(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._alert(node, value=95.2)
        self._alert(node, checker="memory", value=50.0, metric="memory_percent")
        scoped = preview_reeval(ReevalScope.for_node(node))
        wide = preview_node_alert_reeval(node)
        self.assertEqual(
            [(c.alert.pk, c.new_severity, c.new_status) for c in scoped.changes],
            [(c.alert.pk, c.new_severity, c.new_status) for c in wide.changes],
        )
        self.assertEqual([s.alert.pk for s in scoped.skips], [s.alert.pk for s in wide.skips])
        self.assertEqual(scoped.node, wide.node)

    def test_the_audit_records_the_checker_the_operator_asked_for(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._alert(node, value=95.2)
        apply_reeval(ReevalScope.for_checker(node, "cpu"))
        alert.refresh_from_db()
        audit = json.loads(alert.annotations["reevaluated_on_config_change"])
        self.assertEqual(audit["requested_checker"], "cpu")

    def test_a_node_wide_audit_records_no_requested_checker(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._alert(node, value=95.2)
        apply_node_alert_reeval(node)
        alert.refresh_from_db()
        audit = json.loads(alert.annotations["reevaluated_on_config_change"])
        self.assertIsNone(audit["requested_checker"])


class UnregisteredNodeScopeTests(TestCase):
    def setUp(self):
        self.alert = Alert.objects.create(
            fingerprint="cpu-web-99",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            labels={"checker": "cpu", "instance_id": "web-99"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.0})},
        )
        self.incident = Incident.objects.create(title="t", severity="critical", status="open")
        self.incident.alerts.add(self.alert)

    def test_preview_reports_only_skips_and_names_the_unregistered_node(self):
        scope = ReevalScope.for_alert(self.alert)
        self.assertIsNone(scope.node)
        report = preview_reeval(scope)
        self.assertIsNone(report.node)
        self.assertEqual(report.changes, [])
        self.assertEqual(report.skips[0].sentence, "No policy set for cpu on web-99.")

    def test_apply_writes_nothing(self):
        report = apply_reeval(ReevalScope.for_alert(self.alert))
        self.assertEqual(report.changes, [])
        self.assertEqual(len(report.skips), 1)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "firing")
        self.assertEqual(self.alert.severity, "critical")
        self.assertNotIn("reevaluated_on_config_change", self.alert.annotations)
        self.assertFalse(AlertHistory.objects.exists())
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, "open")
        self.assertIsNone(self.incident.resolved_at)


def _primed(report, prime):
    prime(report)
    return report


class ReevalAnnounceTests(TestCase):
    def _node(self, cfg=None):
        return Node.objects.create(instance_id="web-03", config=cfg if cfg is not None else {})

    def _alert(self, node, checker="cpu", metric="cpu_percent", value=95.2, incident=None):
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03",
            source="cluster",
            name=f"{checker} high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            incident=incident,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations={"metrics": json.dumps({metric: value})},
        )

    def _incident(self):
        return Incident.objects.create(title="t", severity="critical", status="open")

    def test_a_resolving_apply_enqueues_one_run_for_the_incident(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        incident = self._incident()
        self._alert(node, incident=incident)

        apply_node_alert_reeval(node)

        runs = PipelineRun.objects.filter(incident_id=incident.pk)
        self.assertEqual(runs.count(), 1)
        run = runs.get()
        self.assertEqual(run.origin, PipelineOrigin.MANUAL)
        self.assertEqual(run.status, PipelineStatus.PENDING)
        self.assertEqual(run.source, "cluster")
        self.assertEqual(run.node, node)
        self.assertEqual(run.inbound_payload, {"downstream_incident_id": incident.pk})
        self.assertEqual(StageExecution.objects.count(), 0)

    def test_a_no_op_apply_enqueues_nothing(self):
        node = self._node()
        apply_node_alert_reeval(node)
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_two_changed_alerts_on_one_incident_enqueue_one_run(self):
        node = self._node(
            {
                "cpu": {"warning_threshold": 99, "critical_threshold": 99},
                "memory": {"warning_threshold": 99, "critical_threshold": 99},
            }
        )
        incident = self._incident()
        self._alert(node, incident=incident)
        self._alert(node, checker="memory", metric="memory_percent", value=80.0, incident=incident)

        report = apply_node_alert_reeval(node)

        self.assertEqual(len(report.changes), 2)
        self.assertEqual(PipelineRun.objects.filter(incident_id=incident.pk).count(), 1)

    def test_two_changed_incidents_promise_and_enqueue_two_runs(self):
        """The counting case, not the dedup case.

        ``run_count`` is printed to the operator as a promise, so an undercount
        has to fail here rather than on the confirm page.
        """
        node = self._node(
            {
                "cpu": {"warning_threshold": 99, "critical_threshold": 99},
                "memory": {"warning_threshold": 99, "critical_threshold": 99},
            }
        )
        first, second = self._incident(), self._incident()
        self._alert(node, incident=first)
        self._alert(node, checker="memory", metric="memory_percent", value=80.0, incident=second)

        preview = preview_node_alert_reeval(node)
        self.assertEqual(preview.run_count, 2)

        report = apply_node_alert_reeval(node)

        self.assertEqual(report.run_count, 2)
        self.assertEqual(PipelineRun.objects.count(), 2)
        self.assertEqual(
            set(PipelineRun.objects.values_list("incident_id", flat=True)),
            {first.pk, second.pk},
        )

    def test_a_changed_alert_with_no_incident_enqueues_nothing(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._alert(node)

        report = apply_node_alert_reeval(node)

        self.assertEqual(len(report.changes), 1)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_a_severity_only_change_enqueues_a_run(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 99}})
        incident = self._incident()
        self._alert(node, incident=incident)

        report = apply_node_alert_reeval(node)

        self.assertEqual(report.resolved_count, 0)
        self.assertEqual(report.severity_changed_count, 1)
        self.assertEqual(PipelineRun.objects.filter(incident_id=incident.pk).count(), 1)
        incident.refresh_from_db()
        self.assertEqual(incident.status, "open")

    def test_a_scope_with_no_node_enqueues_nothing(self):
        alert = Alert.objects.create(
            fingerprint="cpu-web-99",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            incident=self._incident(),
            labels={"checker": "cpu", "instance_id": "web-99"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.0})},
        )

        report = apply_reeval(ReevalScope.for_alert(alert))

        self.assertIsNone(report.node)
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_the_announced_incident_carries_the_status_the_sweep_just_wrote(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        incident = self._incident()
        self._alert(node, incident=incident)
        seen: list[str] = []

        def prime_cached_incidents(report):
            # Populate each alert's cached incident BEFORE the sweep resolves it, so a
            # reload that is not really a reload would hand the announce a stale OPEN.
            for change in report.changes:
                self.assertEqual(change.alert.incident.status, IncidentStatus.OPEN)

        with (
            mock.patch(
                "apps.alerts.reeval_existing.preview_reeval",
                side_effect=lambda scope: _primed(preview_reeval(scope), prime_cached_incidents),
            ),
            mock.patch(
                "apps.alerts.reeval_existing.announce_incident_change",
                side_effect=lambda inc: seen.append(inc.status),
            ),
        ):
            apply_node_alert_reeval(node)

        self.assertEqual(seen, [IncidentStatus.RESOLVED])

    def test_announce_incident_change_enqueues_one_manual_run(self):
        node = self._node()
        incident = self._incident()
        self._alert(node, incident=incident)

        announce_incident_change(incident)

        run = PipelineRun.objects.get()
        self.assertEqual(run.origin, PipelineOrigin.MANUAL)
        self.assertEqual(run.incident_id, incident.pk)
        self.assertEqual(run.source, "cluster")
        self.assertEqual(run.node, node)

    def test_announce_incident_change_on_an_incident_with_no_alerts(self):
        incident = self._incident()

        announce_incident_change(incident)

        run = PipelineRun.objects.get()
        self.assertEqual(run.source, "")
        self.assertIsNone(run.node)


class ReevalIncidentLifecycleTests(TestCase):
    """An applied change moves its incident the way ingest does, or not at all.

    ``apps.alerts.incident_gate.follow_alert`` owns that decision on both write
    paths, so a re-fire here cannot leave a firing alert under a terminal incident,
    and an acknowledged incident absorbs what it absorbs at ingest.
    """

    def _node(self, cfg=None):
        return Node.objects.create(
            instance_id="web-03",
            config=cfg or {"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
        )

    def _alert(self, node, checker="cpu", severity="info", status="resolved", value=95.2, **kw):
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03",
            source="cluster",
            name=kw.pop("name", f"{checker} high"),
            severity=severity,
            status=status,
            started_at=timezone.now(),
            node=node,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": value})},
            **kw,
        )

    def _incident(self, status):
        return Incident.objects.create(title="t", severity="critical", status=status)

    def test_a_refire_reopens_its_resolved_incident(self):
        node = self._node()
        incident = self._incident(IncidentStatus.RESOLVED)
        alert = self._alert(node, incident=incident)

        report = apply_reeval(ReevalScope.for_alert(alert))

        self.assertEqual(report.changes[0].new_status, "firing")
        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertIsNone(incident.resolved_at)
        self.assertEqual(PipelineRun.objects.filter(incident_id=incident.pk).count(), 1)

    def test_a_refire_reopens_its_closed_incident(self):
        node = self._node()
        incident = self._incident(IncidentStatus.CLOSED)
        alert = self._alert(node, incident=incident)

        apply_reeval(ReevalScope.for_alert(alert))

        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertIsNone(incident.closed_at)

    def test_a_refire_joins_an_open_sibling_instead_of_reopening_its_own(self):
        """One situation is one open incident, exactly as on the ingest path."""
        node = self._node()
        terminal = self._incident(IncidentStatus.RESOLVED)
        sibling = self._incident(IncidentStatus.OPEN)
        self._alert(
            node,
            checker="cpu2",
            name="cpu high",
            severity="critical",
            status="firing",
            incident=sibling,
        )
        alert = self._alert(node, incident=terminal)

        apply_reeval(ReevalScope.for_alert(alert))

        alert.refresh_from_db()
        terminal.refresh_from_db()
        self.assertEqual(alert.incident_id, sibling.pk)
        self.assertEqual(terminal.status, IncidentStatus.RESOLVED)
        self.assertEqual(PipelineRun.objects.filter(incident_id=terminal.pk).count(), 0)
        self.assertEqual(PipelineRun.objects.filter(incident_id=sibling.pk).count(), 1)

    def test_an_acknowledged_incident_absorbs_a_de_escalation_and_tells_nobody(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        incident = self._incident(IncidentStatus.ACKNOWLEDGED)
        alert = self._alert(node, severity="critical", status="firing", value=95.2)
        alert.incident = incident
        alert.save()

        report = apply_reeval(ReevalScope.for_alert(alert))

        alert.refresh_from_db()
        self.assertEqual(alert.severity, "warning")
        self.assertFalse(report.changes[0].notify)
        self.assertEqual(report.run_count, 0)
        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.ACKNOWLEDGED)
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_an_acknowledged_incident_breaks_open_on_an_escalation(self):
        node = self._node()
        incident = self._incident(IncidentStatus.ACKNOWLEDGED)
        alert = self._alert(node, severity="warning", status="firing", value=95.2)
        alert.incident = incident
        alert.save()

        report = apply_reeval(ReevalScope.for_alert(alert))

        alert.refresh_from_db()
        self.assertEqual(alert.severity, "critical")
        self.assertTrue(report.changes[0].notify)
        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertEqual(PipelineRun.objects.filter(incident_id=incident.pk).count(), 1)

    def test_a_preview_reports_the_absorbed_change_without_promising_a_run(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 99}})
        incident = self._incident(IncidentStatus.ACKNOWLEDGED)
        alert = self._alert(node, severity="critical", status="firing", value=95.2)
        alert.incident = incident
        alert.save()

        report = preview_reeval(ReevalScope.for_alert(alert))

        self.assertEqual(len(report.changes), 1)
        self.assertEqual(report.run_count, 0)


class ReevalSweepAnnounceTests(TestCase):
    """A sweep resolution is a change too, and gets the run every change gets."""

    def _node(self):
        return Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )

    def _alert(self, node, checker, status="firing", severity="critical", incident=None):
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03",
            source="cluster",
            name=f"{checker} high",
            severity=severity,
            status=status,
            started_at=timezone.now(),
            node=node,
            incident=incident,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.2})},
        )

    def test_an_incident_swept_without_a_change_of_its_own_still_gets_a_run(self):
        node = self._node()
        changed = Incident.objects.create(title="changed", severity="critical", status="open")
        self._alert(node, "cpu", incident=changed)
        stale = Incident.objects.create(title="stale", severity="info", status="open")
        self._alert(node, "memory", status="resolved", severity="info", incident=stale)

        report = apply_node_alert_reeval(node)

        stale.refresh_from_db()
        self.assertEqual(stale.status, IncidentStatus.RESOLVED)
        self.assertEqual(set(report.swept_incident_ids), {changed.pk, stale.pk})
        self.assertEqual(PipelineRun.objects.filter(incident_id=stale.pk).count(), 1)

    def test_the_applied_run_count_equals_the_runs_created(self):
        node = self._node()
        changed = Incident.objects.create(title="changed", severity="critical", status="open")
        self._alert(node, "cpu", incident=changed)
        stale = Incident.objects.create(title="stale", severity="info", status="open")
        self._alert(node, "memory", status="resolved", severity="info", incident=stale)

        report = apply_node_alert_reeval(node)

        self.assertEqual(report.run_count, 2)
        self.assertEqual(PipelineRun.objects.count(), 2)

    def test_an_incident_both_changed_and_swept_gets_exactly_one_run(self):
        node = self._node()
        incident = Incident.objects.create(title="t", severity="critical", status="open")
        self._alert(node, "cpu", incident=incident)

        report = apply_node_alert_reeval(node)

        self.assertEqual(report.run_count, 1)
        self.assertEqual(PipelineRun.objects.filter(incident_id=incident.pk).count(), 1)


class MalformedNodeConfigTests(TestCase):
    """``Node.config`` is never validated at ingest, so it can hold anything."""

    def test_a_config_that_is_not_a_mapping_is_a_malformed_policy_skip(self):
        node = Node.objects.create(instance_id="web-03", config="not a dict")
        Alert.objects.create(
            fingerprint="cpu-web-03",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.2})},
        )

        report = preview_node_alert_reeval(node)

        self.assertEqual(report.changes, [])
        (skip,) = report.skips
        self.assertEqual(skip.reason, SkipReason.MALFORMED_POLICY)


class SkipFixUrlTests(TestCase):
    """A skip an operator can act on says where to act.

    The four policy reasons are fixed in the node's policy editor. The rest are
    facts about the alert or the checker, so they carry no destination rather
    than pointing somewhere that would not help.
    """

    def _node(self, config):
        return Node.objects.create(instance_id="web-03", hostname="web-03", config=config)

    def _alert(self, node, checker="cpu", annotations=None, value=95.0):
        if annotations is None:
            annotations = {"metrics": json.dumps({f"{checker}_percent": value})}
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03",
            source="cluster",
            name=f"{checker} high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations=annotations,
        )

    def _skip(self, node):
        return preview_reeval(ReevalScope.for_node(node)).skips[0]

    def test_no_policy_points_at_the_node_policy_editor(self):
        node = self._node({})
        self._alert(node)
        skip = self._skip(node)
        self.assertEqual(skip.reason, SkipReason.NO_POLICY)
        self.assertEqual(skip.fix_url, f"/admin/alerts/node/{node.pk}/change/")

    def test_incomplete_thresholds_point_at_the_editor(self):
        node = self._node({"cpu": {"warning_threshold": 80}})
        self._alert(node)
        skip = self._skip(node)
        self.assertEqual(skip.reason, SkipReason.INCOMPLETE_THRESHOLDS)
        self.assertEqual(skip.fix_url, f"/admin/alerts/node/{node.pk}/change/")

    def test_inverted_thresholds_point_at_the_editor(self):
        node = self._node({"cpu": {"warning_threshold": 90, "critical_threshold": 10}})
        self._alert(node)
        skip = self._skip(node)
        self.assertEqual(skip.reason, SkipReason.INVERTED_THRESHOLDS)
        self.assertEqual(skip.fix_url, f"/admin/alerts/node/{node.pk}/change/")

    def test_malformed_policy_points_at_the_editor(self):
        node = self._node({"cpu": "not-a-mapping"})
        self._alert(node)
        skip = self._skip(node)
        self.assertEqual(skip.reason, SkipReason.MALFORMED_POLICY)
        self.assertEqual(skip.fix_url, f"/admin/alerts/node/{node.pk}/change/")

    def test_a_checker_with_no_scorer_has_nowhere_to_go(self):
        node = self._node({})
        self._alert(node, checker="raid", annotations={})
        skip = self._skip(node)
        self.assertEqual(skip.reason, SkipReason.NO_SCORER)
        self.assertIsNone(skip.fix_url)

    def test_an_alert_with_no_metrics_has_nowhere_to_go(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 90}})
        self._alert(node, annotations={})
        skip = self._skip(node)
        self.assertEqual(skip.reason, SkipReason.NO_METRICS)
        self.assertIsNone(skip.fix_url)

    def test_an_unchanged_score_has_nothing_to_fix(self):
        node = self._node({"cpu": {"warning_threshold": 80, "critical_threshold": 90}})
        self._alert(node)
        skip = self._skip(node)
        self.assertEqual(skip.reason, SkipReason.UNCHANGED)
        self.assertIsNone(skip.fix_url)

    def test_an_unregistered_node_has_no_editor_to_point_at(self):
        Alert.objects.create(
            fingerprint="cpu-ghost",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            labels={"checker": "cpu", "instance_id": "ghost"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.0})},
        )
        alert = Alert.objects.get(fingerprint="cpu-ghost")
        skip = preview_reeval(ReevalScope.for_alert(alert)).skips[0]
        self.assertIsNone(skip.fix_url)
