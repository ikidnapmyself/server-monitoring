import json
from datetime import datetime, timezone
from unittest import mock

from django.test import TestCase

from apps.alerts.drivers.base import ParsedAlert
from apps.alerts.models import Node
from apps.alerts.reevaluation import (
    PRIMARY_METRIC,
    SkipReason,
    Verdict,
    _score_allowlist,
    _score_numeric,
    allowlist_evaluator,
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


def test_score_numeric_is_the_shared_scorer():
    # value >= crit -> critical; between -> warning; below -> info/resolved
    assert _score_numeric(
        "cpu", {"cpu_percent": 99}, {"warning_threshold": 90, "critical_threshold": 95}
    ) == Verdict("critical", "firing", 99.0)
    assert _score_numeric(
        "cpu", {"cpu_percent": 92}, {"warning_threshold": 90, "critical_threshold": 95}
    ) == Verdict("warning", "firing", 92.0)
    assert _score_numeric(
        "cpu", {"cpu_percent": 50}, {"warning_threshold": 90, "critical_threshold": 95}
    ) == Verdict("info", "resolved", 50.0)
    # fail-open cases, each naming its own reason
    assert (
        _score_numeric(
            "cpu",
            {"cpu_percent": 99},
            {"warning_threshold": True, "critical_threshold": True},
        ).reason
        is SkipReason.INCOMPLETE_THRESHOLDS
    )
    assert (
        _score_numeric(
            "cpu", {"cpu_percent": 99}, {"warning_threshold": 90, "critical_threshold": 50}
        ).reason
        is SkipReason.INVERTED_THRESHOLDS
    )
    assert (
        _score_numeric(
            "cpu", {"other": 1}, {"warning_threshold": 90, "critical_threshold": 95}
        ).reason
        is SkipReason.NO_METRIC_VALUE
    )
    assert (
        _score_numeric(
            "unknown", {"x": 1}, {"warning_threshold": 90, "critical_threshold": 95}
        ).reason
        is SkipReason.NO_PRIMARY_METRIC
    )
    assert (
        _score_numeric("cpu", {"cpu_percent": 99}, "not-a-dict").reason
        is SkipReason.MALFORMED_POLICY
    )
    assert _score_numeric("cpu", {"cpu_percent": 99}, None).reason is SkipReason.NO_POLICY
    # non-dict metrics (defensive guard on the shared scorer)
    assert (
        _score_numeric(
            "cpu", "not-a-dict", {"warning_threshold": 90, "critical_threshold": 95}
        ).reason
        is SkipReason.NO_METRICS
    )


def test_score_allowlist_all_ports_allowed_resolves():
    metrics = {"listening": [{"port": 22, "exposed": True}, {"port": 80, "exposed": True}]}
    assert _score_allowlist("listening_ports", metrics, {"allowlist": [22, 80]}) == Verdict(
        "info", "resolved", 0.0
    )


def test_score_allowlist_unexpected_port_fires():
    metrics = {"listening": [{"port": 22, "exposed": True}, {"port": 9999, "exposed": True}]}
    assert _score_allowlist("listening_ports", metrics, {"allowlist": [22]}) == Verdict(
        "warning", "firing", 1.0
    )


def test_score_allowlist_empty_allowlist_flags_only_exposed():
    # No allowlist configured -> only externally-exposed (non-loopback) ports flag.
    metrics = {"listening": [{"port": 22, "exposed": False}, {"port": 9999, "exposed": True}]}
    assert _score_allowlist("listening_ports", metrics, {"allowlist": []}) == Verdict(
        "warning", "firing", 1.0
    )


def test_score_allowlist_empty_allowlist_all_loopback_resolves():
    metrics = {"listening": [{"port": 22, "exposed": False}]}
    assert _score_allowlist("listening_ports", metrics, {"allowlist": []}) == Verdict(
        "info", "resolved", 0.0
    )


def test_allowlist_evaluator_resolves_when_covered():
    parsed = _alert("listening_ports", '{"listening": [{"port": 22, "exposed": true}]}')
    assert allowlist_evaluator(parsed, {"allowlist": [22]}) == Verdict("info", "resolved", 0.0)


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
    assert out == Verdict("info", "resolved", 95.2)


def test_numeric_evaluator_warning_band():
    parsed = _alert("cpu", '{"cpu_percent": 85}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out == Verdict("warning", "firing", 85.0)


def test_numeric_evaluator_critical():
    parsed = _alert("disk_temp", '{"hottest_c": 70}')
    out = numeric_evaluator(parsed, {"warning_threshold": 60, "critical_threshold": 68})
    assert out == Verdict("critical", "firing", 70.0)


def test_numeric_evaluator_value_equals_critical_threshold():
    # value exactly == critical_threshold pins the >= contract for critical.
    parsed = _alert("cpu", '{"cpu_percent": 99}')
    out = numeric_evaluator(parsed, {"warning_threshold": 99, "critical_threshold": 99})
    assert out == Verdict("critical", "firing", 99.0)


def test_numeric_evaluator_value_equals_warning_threshold():
    # value exactly == warning_threshold (below critical) pins >= for warning.
    parsed = _alert("cpu", '{"cpu_percent": 80}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out == Verdict("warning", "firing", 80.0)


def test_numeric_evaluator_unknown_checker_skips():
    parsed = _alert("not_a_checker", '{"cpu_percent": 95}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out.reason is SkipReason.NO_PRIMARY_METRIC


def test_numeric_evaluator_bool_thresholds_skip():
    # bool is an int subclass; a boolean threshold is malformed config -> passthrough.
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    out = numeric_evaluator(parsed, {"warning_threshold": True, "critical_threshold": True})
    assert out.reason is SkipReason.INCOMPLETE_THRESHOLDS


def test_numeric_evaluator_inverted_thresholds_skip():
    # critical below warning is nonsensical config -> passthrough.
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    out = numeric_evaluator(parsed, {"warning_threshold": 90, "critical_threshold": 50})
    assert out.reason is SkipReason.INVERTED_THRESHOLDS


def test_numeric_evaluator_missing_metric_skips():
    parsed = _alert("cpu", '{"other": 1}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out.reason is SkipReason.NO_METRIC_VALUE


def test_numeric_evaluator_malformed_metrics_skip():
    parsed = _alert("cpu", "not json")
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out.reason is SkipReason.NO_METRICS


def test_numeric_evaluator_no_metrics_annotation_skips():
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    parsed.annotations = {}
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out.reason is SkipReason.NO_METRICS


def test_numeric_evaluator_metrics_not_a_dict_skips():
    parsed = _alert("cpu", "[1, 2, 3]")
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out.reason is SkipReason.NO_METRICS


def test_numeric_evaluator_non_numeric_value_skips():
    parsed = _alert("cpu", '{"cpu_percent": "high"}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out.reason is SkipReason.NO_METRIC_VALUE


def test_numeric_evaluator_boolean_value_skips():
    parsed = _alert("cpu", '{"cpu_percent": true}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80, "critical_threshold": 95})
    assert out.reason is SkipReason.NO_METRIC_VALUE


def test_numeric_evaluator_missing_thresholds_skip():
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    out = numeric_evaluator(parsed, {"warning_threshold": 80})
    assert out.reason is SkipReason.INCOMPLETE_THRESHOLDS


def test_numeric_evaluator_non_dict_cfg_skips():
    parsed = _alert("cpu", '{"cpu_percent": 95}')
    assert numeric_evaluator(parsed, "99").reason is SkipReason.MALFORMED_POLICY
    assert numeric_evaluator(parsed, [1, 2]).reason is SkipReason.MALFORMED_POLICY
    assert numeric_evaluator(parsed, None).reason is SkipReason.NO_POLICY


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

    def test_listening_ports_resolves_when_allowlist_covers(self):
        Node.objects.create(
            instance_id="web-03", config={"listening_ports": {"allowlist": [22, 80]}}
        )
        parsed = self._alert(
            "listening_ports",
            '{"listening": [{"port": 22, "exposed": true}, {"port": 80, "exposed": true}]}',
            severity="warning",
        )
        out = reevaluate_severity(parsed)
        self.assertEqual(out.severity, "info")
        self.assertEqual(out.status, "resolved")
        self.assertIsNotNone(out.ended_at)
        audit = json.loads(out.annotations["severity_reevaluated"])
        self.assertEqual(audit["checker"], "listening_ports")
        self.assertEqual(audit["to"], "info")
        self.assertEqual(audit["value"], 0.0)

    def test_listening_ports_still_flagged_passthrough(self):
        Node.objects.create(instance_id="web-03", config={"listening_ports": {"allowlist": [22]}})
        parsed = self._alert(
            "listening_ports",
            '{"listening": [{"port": 9999, "exposed": true}]}',
            severity="warning",
        )
        out = reevaluate_severity(parsed)
        self.assertEqual(out.severity, "warning")
        self.assertEqual(out.status, "firing")
        self.assertNotIn("severity_reevaluated", out.annotations)

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

    def test_non_dict_labels_pass_through_without_raising(self):
        # A truthy non-dict labels raises inside the body (labels.get); the
        # fail-open guard swallows it and the log-context falls back to "?".
        alert = self._alert("cpu", '{"cpu_percent": 95.2}')
        alert.labels = "not-a-dict"
        out = reevaluate_severity(alert)
        self.assertIs(out, alert)
        self.assertEqual(out.severity, "critical")


class OutcomeTypeTests(TestCase):
    def test_verdict_carries_the_score(self):
        from apps.alerts.reevaluation import Verdict

        verdict = Verdict(severity="warning", status="firing", value=91.3)
        self.assertEqual(verdict.severity, "warning")
        self.assertEqual(verdict.status, "firing")
        self.assertEqual(verdict.value, 91.3)

    def test_skip_carries_a_reason_and_context(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        skip = Skip(SkipReason.NO_METRICS)
        self.assertEqual(skip.reason, SkipReason.NO_METRICS)
        self.assertEqual(skip.context, {})

    def test_skip_context_is_keyword_only(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        skip = Skip(SkipReason.UNCHANGED, value=41.2, warning=99.0)
        self.assertEqual(skip.context["value"], 41.2)
        self.assertEqual(skip.context["warning"], 99.0)


class ScoreNumericReasonTests(TestCase):
    def _score(self, cfg, metrics=None, checker="cpu"):
        return _score_numeric(checker, metrics if metrics is not None else {}, cfg)

    def test_missing_policy_says_so(self):
        self.assertEqual(self._score(None).reason, SkipReason.NO_POLICY)

    def test_non_mapping_policy_is_malformed(self):
        self.assertEqual(self._score("99").reason, SkipReason.MALFORMED_POLICY)

    def test_half_filled_thresholds_are_incomplete(self):
        skip = self._score({"warning_threshold": 90})
        self.assertEqual(skip.reason, SkipReason.INCOMPLETE_THRESHOLDS)

    def test_inverted_thresholds_say_so(self):
        skip = self._score({"warning_threshold": 90, "critical_threshold": 80})
        self.assertEqual(skip.reason, SkipReason.INVERTED_THRESHOLDS)
        self.assertEqual(skip.context["warning"], 90.0)
        self.assertEqual(skip.context["critical"], 80.0)

    def test_unknown_checker_has_no_primary_metric(self):
        skip = self._score({"warning_threshold": 1, "critical_threshold": 2}, checker="raid")
        self.assertEqual(skip.reason, SkipReason.NO_PRIMARY_METRIC)

    def test_non_mapping_metrics_are_missing(self):
        skip = self._score({"warning_threshold": 1, "critical_threshold": 2}, metrics="x")
        self.assertEqual(skip.reason, SkipReason.NO_METRICS)

    def test_absent_metric_value_says_which_key(self):
        skip = self._score({"warning_threshold": 1, "critical_threshold": 2}, metrics={})
        self.assertEqual(skip.reason, SkipReason.NO_METRIC_VALUE)
        self.assertEqual(skip.context["metric_key"], "cpu_percent")

    def test_a_score_in_the_warning_band_is_a_firing_verdict(self):
        outcome = self._score(
            {"warning_threshold": 90, "critical_threshold": 95},
            metrics={"cpu_percent": 91.5},
        )
        self.assertIsInstance(outcome, Verdict)
        self.assertEqual(outcome.severity, "warning")
        self.assertEqual(outcome.status, "firing")
        self.assertEqual(outcome.value, 91.5)


class ScoreAllowlistReasonTests(TestCase):
    def _score(self, cfg, metrics=None):
        return _score_allowlist(
            "listening_ports",
            metrics if metrics is not None else {"listening": [{"port": 22, "exposed": True}]},
            cfg,
        )

    def test_missing_policy_says_so(self):
        skip = self._score(None)
        self.assertEqual(skip.reason, SkipReason.NO_POLICY)
        self.assertEqual(skip.context["checker"], "listening_ports")

    def test_non_mapping_policy_is_malformed(self):
        self.assertEqual(self._score("nope").reason, SkipReason.MALFORMED_POLICY)

    def test_non_mapping_metrics_are_missing(self):
        skip = self._score({"allowlist": [22]}, metrics="x")
        self.assertEqual(skip.reason, SkipReason.NO_METRICS)

    def test_absent_allowlist_is_malformed(self):
        self.assertEqual(self._score({}).reason, SkipReason.MALFORMED_POLICY)

    def test_non_list_allowlist_is_malformed(self):
        self.assertEqual(self._score({"allowlist": "x"}).reason, SkipReason.MALFORMED_POLICY)

    def test_non_numeric_allowlist_entry_is_malformed(self):
        self.assertEqual(self._score({"allowlist": ["22"]}).reason, SkipReason.MALFORMED_POLICY)

    def test_bool_allowlist_entry_is_malformed(self):
        self.assertEqual(self._score({"allowlist": [True]}).reason, SkipReason.MALFORMED_POLICY)

    def test_absent_listening_inventory_says_which_key(self):
        skip = self._score({"allowlist": [22]}, metrics={"other": 1})
        self.assertEqual(skip.reason, SkipReason.NO_METRIC_VALUE)
        self.assertEqual(skip.context["metric_key"], "listening")

    def test_non_list_listening_inventory_says_which_key(self):
        skip = self._score({"allowlist": [22]}, metrics={"listening": "x"})
        self.assertEqual(skip.reason, SkipReason.NO_METRIC_VALUE)
        self.assertEqual(skip.context["metric_key"], "listening")

    def test_malformed_listening_entry_says_which_key(self):
        skip = self._score({"allowlist": [22]}, metrics={"listening": [1]})
        self.assertEqual(skip.reason, SkipReason.NO_METRIC_VALUE)
        self.assertEqual(skip.context["metric_key"], "listening")

    def test_malformed_port_says_which_key(self):
        skip = self._score({"allowlist": [22]}, metrics={"listening": [{"port": "x"}]})
        self.assertEqual(skip.reason, SkipReason.NO_METRIC_VALUE)
        self.assertEqual(skip.context["metric_key"], "listening")

    def test_a_flagged_port_is_a_firing_verdict(self):
        outcome = self._score(
            {"allowlist": [22]},
            metrics={"listening": [{"port": 9999, "exposed": True}]},
        )
        self.assertIsInstance(outcome, Verdict)
        self.assertEqual(outcome.severity, "warning")
        self.assertEqual(outcome.status, "firing")
        self.assertEqual(outcome.value, 1.0)

    def test_a_covered_inventory_is_a_resolved_verdict(self):
        outcome = self._score({"allowlist": [22]})
        self.assertIsInstance(outcome, Verdict)
        self.assertEqual(outcome.severity, "info")
        self.assertEqual(outcome.status, "resolved")
        self.assertEqual(outcome.value, 0.0)


class AllowlistEvaluatorReasonTests(TestCase):
    def test_unparseable_metrics_say_no_metrics(self):
        parsed = _alert("listening_ports", "not json")
        skip = allowlist_evaluator(parsed, {"allowlist": [22]})
        self.assertEqual(skip.reason, SkipReason.NO_METRICS)
        self.assertEqual(skip.context["checker"], "listening_ports")
