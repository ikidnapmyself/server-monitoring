"""Re-evaluation in words: the records an alert carries and the preview of a re-score."""

import json

import pytest
from django.test import TestCase
from django.utils import timezone

from apps.alerts.describing import (
    REOPEN_WARNING,
    UNREADABLE,
    alert_link,
    alert_name,
    describe_reeval,
    describe_reeval_applied,
    describe_reeval_consequences,
    describe_reeval_report,
)
from apps.alerts.models import Alert, Node
from apps.alerts.reeval_existing import AlertChange, AlertSkip, ReevalReport
from apps.alerts.reevaluation import SkipReason
from config.describing import Action

INGEST_RECORD = {
    "from": "critical",
    "to": "warning",
    "status_from": "firing",
    "status_to": "firing",
    "value": 91.3,
    "thresholds": {"warning_threshold": 90, "critical_threshold": 99},
    "checker": "cpu",
    "by": "hub-node-policy",
}

CONFIG_CHANGE_RECORD = {
    "from": "warning",
    "to": "info",
    "status_from": "firing",
    "status_to": "resolved",
    "value": 41.2,
    "thresholds": {"warning_threshold": 80, "critical_threshold": 95},
    "checker": "cpu",
    "requested_checker": "cpu",
    "by": "hub-node-policy:config-change",
    "at": "2026-08-04T09:15:00+00:00",
}


def alert_with(**annotations) -> Alert:
    return Alert.objects.create(
        fingerprint="check:web-03:cpu",
        source="cluster",
        name="cpu high",
        severity="warning",
        status="firing",
        started_at=timezone.now(),
        labels={"checker": "cpu", "instance_id": "web-03"},
        annotations={key: json.dumps(value) for key, value in annotations.items()},
    )


def _values(alert) -> list[str]:
    return [fact.value for fact in describe_reeval(alert)]


def _text(alert) -> str:
    return " ".join(f"{fact.label}: {fact.value}" for fact in describe_reeval(alert))


class DescribeReevalTests(TestCase):
    def test_ingest_record_renders_change_value_and_policy(self):
        panel = _text(alert_with(severity_reevaluated=INGEST_RECORD))
        self.assertIn("At ingest", panel)
        self.assertIn("CRITICAL to WARNING", panel)
        self.assertIn("value 91.3", panel)
        self.assertIn("policy warning 90 / critical 99", panel)

    def test_config_change_record_is_labelled_as_the_operator_path(self):
        panel = _text(alert_with(reevaluated_on_config_change=CONFIG_CHANGE_RECORD))
        self.assertIn("On a policy change", panel)
        self.assertNotIn("At ingest", panel)
        self.assertIn("WARNING to INFO", panel)
        self.assertIn("4 Aug 2026", panel)
        self.assertIn("status firing to resolved", panel)
        self.assertIn("value 41.2", panel)
        self.assertIn("policy warning 80 / critical 95", panel)

    def test_both_records_render_distinguishably(self):
        facts = describe_reeval(
            alert_with(
                severity_reevaluated=INGEST_RECORD,
                reevaluated_on_config_change=CONFIG_CHANGE_RECORD,
            )
        )
        self.assertEqual([f.label for f in facts], ["At ingest", "On a policy change"])
        self.assertIn("CRITICAL to WARNING", facts[0].value)
        self.assertIn("WARNING to INFO", facts[1].value)

    def test_no_record_renders_never_re_evaluated(self):
        self.assertEqual(describe_reeval(alert_with()), ())

    def test_annotations_not_a_dict_renders_never_re_evaluated(self):
        alert = alert_with()
        alert.annotations = None
        self.assertEqual(describe_reeval(alert), ())

    def test_malformed_json_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": "not json at all"}
        self.assertIn(UNREADABLE, _values(alert))

    def test_non_string_annotation_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": {"from": "critical"}}
        self.assertIn(UNREADABLE, _values(alert))

    def test_record_parsing_to_a_list_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": "[]"}
        self.assertIn(UNREADABLE, _values(alert))

    def test_record_parsing_to_a_number_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": "3"}
        self.assertIn(UNREADABLE, _values(alert))

    def test_empty_record_renders_the_fallback(self):
        panel = _text(alert_with(severity_reevaluated={}))
        self.assertIn(UNREADABLE, panel)

    def test_record_without_value_or_thresholds_renders_what_it_has(self):
        panel = _text(alert_with(severity_reevaluated={"from": "critical", "to": "warning"}))
        self.assertIn("CRITICAL to WARNING.", panel)
        self.assertNotIn("value", panel)
        self.assertNotIn("policy", panel)

    def test_record_without_a_severity_change_still_renders_its_date(self):
        panel = _text(alert_with(reevaluated_on_config_change={"at": "2026-08-04T09:15:00+00:00"}))
        self.assertIn("on 4 Aug 2026.", panel)

    def test_unparseable_timestamp_is_dropped_not_raised(self):
        panel = _text(
            alert_with(reevaluated_on_config_change=dict(CONFIG_CHANGE_RECORD, at="last Tuesday"))
        )
        self.assertIn("WARNING to INFO", panel)
        self.assertNotIn(" on ", panel)

    def test_impossible_timestamp_is_dropped_not_raised(self):
        panel = _text(
            alert_with(
                reevaluated_on_config_change=dict(CONFIG_CHANGE_RECORD, at="2026-13-45T99:00:00")
            )
        )
        self.assertIn("WARNING to INFO", panel)
        self.assertNotIn(" on ", panel)

    def test_naive_timestamp_renders_without_conversion(self):
        panel = _text(
            alert_with(reevaluated_on_config_change=dict(CONFIG_CHANGE_RECORD, at="2026-08-04"))
        )
        self.assertIn("4 Aug 2026", panel)

    def test_thresholds_of_the_wrong_shape_drop_the_policy_clause(self):
        panel = _text(alert_with(severity_reevaluated=dict(INGEST_RECORD, thresholds="90/99")))
        self.assertIn("CRITICAL to WARNING", panel)
        self.assertNotIn("policy", panel)

    def test_thresholds_holding_neither_thresholds_nor_allowlist_drop_the_clause(self):
        panel = _text(
            alert_with(severity_reevaluated=dict(INGEST_RECORD, thresholds={"enabled": True}))
        )
        self.assertNotIn("policy", panel)

    def test_listening_ports_renders_its_allowlist_not_thresholds(self):
        panel = _text(
            alert_with(
                severity_reevaluated={
                    "from": "warning",
                    "to": "info",
                    "value": 0,
                    "thresholds": {"allowlist": [22, 80, 443]},
                    "checker": "listening_ports",
                }
            )
        )
        self.assertIn("policy allows ports 22, 80, 443", panel)
        self.assertNotIn("warning 90", panel)

    def test_empty_allowlist_says_so(self):
        panel = _text(
            alert_with(
                severity_reevaluated=dict(INGEST_RECORD, thresholds={"allowlist": []}),
            )
        )
        self.assertIn("policy allows no ports", panel)

    def test_values_render_at_the_confirm_page_precision(self):
        panel = _text(
            alert_with(
                severity_reevaluated=dict(
                    INGEST_RECORD,
                    value=41.199999999999996,
                    thresholds={"warning_threshold": 79.950000000000003},
                )
            )
        )
        self.assertIn("value 41.2", panel)
        self.assertIn("warning 80.0", panel)
        self.assertNotIn("41.199", panel)

    def test_policy_values_from_config_pass_through_as_sent(self):
        panel = _text(
            alert_with(
                severity_reevaluated=dict(
                    INGEST_RECORD, thresholds={"allowlist": ["<script>alert(1)</script>"]}
                )
            )
        )
        self.assertIn("policy allows ports <script>alert(1)</script>", panel)


def _change(**fields):
    fields = {
        "alert": Alert(),
        "old_severity": "critical",
        "old_status": "firing",
        "new_severity": "warning",
        "new_status": "firing",
        "value": 72.04,
        **fields,
    }
    return AlertChange(**fields)


def test_report_words_each_change():
    report = ReevalReport(
        node=None,
        changes=[
            _change(),
            _change(new_status="resolved", new_severity="info"),
            _change(old_status="resolved", new_severity="critical", notify=False),
        ],
    )
    rows = describe_reeval_report(report)
    assert [r.headline for r in rows] == [
        "Would move this alert from critical to warning",
        "Would resolve this alert",
        "Would reopen this alert at critical",
    ]
    assert rows[0].detail == "Its stored value is 72.0."
    assert rows[2].detail.endswith("Its incident is acknowledged, so nobody is notified.")
    assert [r.tone for r in rows] == ["warning", "ok", "critical"]


@pytest.mark.django_db
def test_report_offers_the_node_only_for_a_skip_an_editor_can_fix():
    node = Node.objects.create(instance_id="i-1", hostname="web")
    alert = Alert.objects.create(
        fingerprint="check:i-1:cpu",
        name="cpu",
        severity="warning",
        source="cluster",
        node=node,
        labels={"checker": "cpu"},
        started_at=timezone.now(),
    )
    report = ReevalReport(
        node=node,
        skips=[
            AlertSkip(alert=alert, reason=SkipReason.NO_POLICY, sentence="No policy."),
            AlertSkip(alert=alert, reason=SkipReason.NO_SCORER, sentence="No scorer."),
        ],
    )
    fixable, unfixable = describe_reeval_report(report)
    assert fixable.action == Action(
        "Edit the node's policy", "edit_node_policy", {"pk": node.pk, "checker": "cpu"}
    )
    assert unfixable.action is None and unfixable.tone == "info"


def test_a_skip_on_an_unregistered_node_offers_nothing():
    report = ReevalReport(
        node=None,
        skips=[AlertSkip(alert=None, reason=SkipReason.NO_POLICY, sentence="No policy.")],
    )
    (row,) = describe_reeval_report(report)
    assert row.action is None and row.headline == "No policy."


def test_applied_counts_what_changed():
    report = ReevalReport(node=None, changes=[_change(new_status="resolved")])
    assert describe_reeval_applied(report).startswith("Resolved 1, changed severity on 0.")


def test_applied_names_the_runs_it_queued():
    assert describe_reeval_applied(ReevalReport(node=None)).endswith("Queued 0 pipeline runs.")


def test_reopen_warning_has_no_identifiers():
    assert "{" not in REOPEN_WARNING and "_" not in REOPEN_WARNING


def test_a_named_report_leads_each_change_with_its_alert_and_links_it():
    change = _change()
    (row,) = describe_reeval_report(ReevalReport(node=None, changes=[change]), named=True)
    assert row.headline.startswith(f"{alert_name(change.alert)}: would ")
    assert row.action == alert_link(change.alert.pk)


def test_an_unnamed_report_is_unchanged():
    (row,) = describe_reeval_report(ReevalReport(node=None, changes=[_change()]))
    assert row.headline.startswith("Would ")
    assert row.action is None


def test_consequences_name_the_runs_and_the_sweep():
    report = ReevalReport(node=None, changes=[_change(new_status="resolved")])
    lines = describe_reeval_consequences(report)
    assert any(line.startswith("Applying queues ") for line in lines)
    assert any("sweeps this node" in line for line in lines)
    assert describe_reeval_consequences(ReevalReport(node=None)) == ()
