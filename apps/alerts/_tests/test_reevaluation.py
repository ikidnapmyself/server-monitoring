from datetime import datetime, timezone

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
    assert out == ("info", "resolved")


def test_numeric_evaluator_warning_band():
    parsed = _alert("cpu", '{"cpu_percent": 85}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out == ("warning", "firing")


def test_numeric_evaluator_critical():
    parsed = _alert("disk_temp", '{"hottest_c": 70}')
    out = numeric_evaluator(parsed, {"warning_threshold": 60, "critical_threshold": 68})
    assert out == ("critical", "firing")


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
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95.2}'))
        self.assertEqual(out.severity, "info")
        self.assertEqual(out.status, "resolved")
        self.assertIn("severity_reevaluated", out.annotations)
        self.assertIsNotNone(out.ended_at)

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

    def test_no_change_leaves_annotations_untouched(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 90}},
        )
        out = reevaluate_severity(self._alert("cpu", '{"cpu_percent": 95}'))
        self.assertEqual(out.severity, "critical")  # already critical, still firing
        self.assertNotIn("severity_reevaluated", out.annotations)

    def test_resolved_alert_not_re_ended_when_already_ended(self):
        # Downgrade an already-resolved alert to warning/firing: status changes
        # firing, but no ended_at manipulation on the resolved->firing direction.
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}},
        )
        parsed = self._alert("cpu", '{"cpu_percent": 85}', severity="info", status="resolved")
        out = reevaluate_severity(parsed)
        self.assertEqual(out.severity, "warning")
        self.assertEqual(out.status, "firing")
        self.assertIn("severity_reevaluated", out.annotations)
