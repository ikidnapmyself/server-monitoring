import json
from datetime import datetime, timezone
from unittest import mock

from django.test import TestCase

from apps.alerts.drivers.base import ParsedAlert
from apps.alerts.models import Node
from apps.alerts.reevaluation import (
    PRIMARY_METRIC,
    numeric_evaluator,
    reevaluate_severity,
)


def _alert(checker, metrics_json, severity="critical", status="firing"):
    return ParsedAlert(
        fingerprint="fp",
        name="n",
        status=status,
        started_at=datetime.now(timezone.utc),
        severity=severity,
        labels={"checker": checker, "instance_id": "web-03"},
        annotations={"metrics": metrics_json},
    )


def test_primary_metric_covers_seven_numeric_checkers():
    assert set(PRIMARY_METRIC) == {
        "cpu",
        "memory",
        "disk",
        "disk_inodes",
        "disk_temp",
        "cpu_temp",
        "io_strain",
    }


def test_numeric_evaluator_below_thresholds_is_ok_resolved():
    parsed = _alert("cpu", '{"cpu_percent": 95.2}')
    out = numeric_evaluator(parsed, {"warning_threshold": 99, "critical_threshold": 99})
    assert out == ("info", "resolved", 95.2)


def test_numeric_evaluator_warning_band():
    parsed = _alert("cpu", '{"cpu_percent": 85}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out == ("warning", "firing", 85.0)


def test_numeric_evaluator_critical():
    parsed = _alert("disk_temp", '{"hottest_c": 70}')
    out = numeric_evaluator(parsed, {"warning_threshold": 60, "critical_threshold": 68})
    assert out == ("critical", "firing", 70.0)


def test_numeric_evaluator_value_equals_critical_threshold():
    # value exactly == critical_threshold pins the >= contract for critical.
    parsed = _alert("cpu", '{"cpu_percent": 99}')
    out = numeric_evaluator(parsed, {"warning_threshold": 99, "critical_threshold": 99})
    assert out == ("critical", "firing", 99.0)


def test_numeric_evaluator_value_equals_warning_threshold():
    # value exactly == warning_threshold (below critical) pins >= for warning.
    parsed = _alert("cpu", '{"cpu_percent": 80}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out == ("warning", "firing", 80.0)


def test_numeric_evaluator_unknown_checker_returns_none():
    parsed = _alert("not_a_checker", '{"cpu_percent": 95}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_missing_metric_returns_none():
    parsed = _alert("cpu", '{"other": 1}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_malformed_metrics_returns_none():
    parsed = _alert("cpu", "not json")
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_no_metrics_annotation_returns_none():
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    parsed.annotations = {}
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_metrics_not_a_dict_returns_none():
    parsed = _alert("cpu", "[1, 2, 3]")
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_non_numeric_value_returns_none():
    parsed = _alert("cpu", '{"cpu_percent": "high"}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_boolean_value_returns_none():
    parsed = _alert("cpu", '{"cpu_percent": true}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95}) is None


def test_numeric_evaluator_missing_thresholds_returns_none():
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    assert numeric_evaluator(parsed, {"warning_threshold": 80}) is None


def test_numeric_evaluator_non_dict_cfg_returns_none():
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    assert numeric_evaluator(parsed, "99") is None
    assert numeric_evaluator(parsed, [1, 2]) is None


class ReevaluateSeverityTests(TestCase):
    def _alert(
        self,
        checker,
        metrics_json,
        instance_id="web-03",
        severity="critical",
        status="firing",
        labels=None,
    ):
        base = {"checker": checker, "instance_id": instance_id}
        if labels is not None:
            base = labels
        return ParsedAlert(
            fingerprint="fp",
            name="n",
            status=status,
            started_at=datetime.now(timezone.utc),
            severity=severity,
            labels=base,
            annotations={"metrics": metrics_json},
        )

    def test_downgrades_firing_to_resolved(self):
        thresholds = {"warning_threshold": 99, "critical_threshold": 99}
        Node.objects.create(instance_id="web-03", config={"cpu": thresholds})
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "info")
        self.assertEqual(out.status, "resolved")
        self.assertIn("severity_reevaluated", out.annotations)
        self.assertIsNotNone(out.ended_at)

        audit = json.loads(out.annotations["severity_reevaluated"])
        self.assertEqual(audit["from"], "critical")
        self.assertEqual(audit["to"], "info")
        self.assertEqual(audit["status_from"], "firing")
        self.assertEqual(audit["status_to"], "resolved")
        self.assertEqual(audit["value"], 95.2)
        self.assertEqual(audit["thresholds"], thresholds)
        self.assertEqual(audit["checker"], "cpu")
        self.assertEqual(audit["by"], "hub-node-policy")

    def test_no_node_config_passthrough(self):
        Node.objects.create(instance_id="web-03")  # empty config
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "critical")
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_unknown_node_passthrough(self):
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "critical")

    def test_non_numeric_checker_passthrough(self):
        Node.objects.create(instance_id="web-03", config={"raid": {"x": 1}})
        out = reevaluate_severity(self._alert("raid", '{"array_count": 1}'))
        self.assertEqual(out.severity, "critical")

    def test_missing_checker_label_passthrough(self):
        out = reevaluate_severity(
            self._alert("cpu", '{"cpu_percent": 95}', labels={"instance_id": "web-03"})
        )
        self.assertEqual(out.severity, "critical")

    def test_missing_instance_label_passthrough(self):
        out = reevaluate_severity(
            self._alert("cpu", '{"cpu_percent": 95}', labels={"checker": "cpu"})
        )
        self.assertEqual(out.severity, "critical")

    def test_missing_labels_passthrough(self):
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95}', labels={}))
        self.assertEqual(out.severity, "critical")

    def test_invalid_metrics_returns_none_passthrough(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
        )
        out = reevaluate_severity(self._alert("cpu", "not json"))
        self.assertEqual(out.severity, "critical")
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_non_dict_config_string_passthrough(self):
        Node.objects.create(instance_id="web-03", config={"cpu": "99"})
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "critical")
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_non_dict_config_list_passthrough(self):
        Node.objects.create(instance_id="web-03", config={"cpu": [1, 2]})
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "critical")
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_empty_dict_config_passthrough(self):
        Node.objects.create(instance_id="web-03", config={"cpu": {}})
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "critical")
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_no_change_leaves_annotations_untouched(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
        )
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95}'))
        self.assertEqual(out.severity, "critical")  # already critical, still firing
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_upgrade_resolved_to_firing_clears_ended_at(self):
        # Node said resolved (with a stale ended_at); hub policy makes it critical.
        # The re-evaluated firing status must clear ended_at, not persist it.
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}},
        )
        parsed = self._alert("cpu", '{"cpu_percent": 97}', severity="info", status="resolved")
        parsed.ended_at = datetime.now(timezone.utc)
        out = reevaluate_severity(parsed)
        self.assertEqual(out.severity, "critical")
        self.assertEqual(out.status, "firing")
        self.assertIsNone(out.ended_at)

    def test_resolved_to_firing_clears_ended_at_warning_band(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}},
        )
        parsed = self._alert("cpu", '{"cpu_percent": 85}', severity="info", status="resolved")
        parsed.ended_at = datetime.now(timezone.utc)
        out = reevaluate_severity(parsed)
        self.assertEqual(out.severity, "warning")
        self.assertEqual(out.status, "firing")
        self.assertIsNone(out.ended_at)
        self.assertIn("severity_reevaluated", out.annotations)

    def test_already_resolved_severity_change_keeps_existing_ended_at(self):
        # info+resolved -> warning would be firing; to exercise the resolved branch
        # where ended_at is already set, downgrade a firing/critical alert to
        # resolved but pre-set ended_at so the "elif ended_at is None" is False.
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        preset = datetime.now(timezone.utc)
        parsed = self._alert("cpu", '{"cpu_percent": 50}', severity="critical", status="resolved")
        parsed.ended_at = preset
        out = reevaluate_severity(parsed)
        self.assertEqual(out.severity, "info")
        self.assertEqual(out.status, "resolved")
        self.assertEqual(out.ended_at, preset)  # not overwritten

    def test_exception_in_body_passes_through(self):
        # Force a raise from inside the re-eval body (DB lookup) and assert
        # reevaluate_severity swallows it and returns the alert unchanged.
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._alert("cpu", '{"cpu_percent": 95.2}')
        with mock.patch("apps.alerts.models.Node.objects.filter", side_effect=RuntimeError("boom")):
            out = reevaluate_severity(alert)
        self.assertIs(out, alert)
        self.assertEqual(out.severity, "critical")
        self.assertNotIn("severity_reevaluated", out.annotations)
