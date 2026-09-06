"""The Re-evaluation panel on AlertAdmin.

The operator complaint this closes: "I can't see if a re-evaluation applied." Both
writers stored their record as raw JSON inside the collapsed Metadata fieldset, which
answers the question only for someone willing to read JSON.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert
from apps.alerts.reeval_display import NEVER, UNREADABLE, reeval_panel

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


class ReevalPanelTests(TestCase):
    def test_ingest_record_renders_change_value_and_policy(self):
        panel = reeval_panel(alert_with(severity_reevaluated=INGEST_RECORD))
        self.assertIn("At ingest", panel)
        self.assertIn("CRITICAL to WARNING", panel)
        self.assertIn("value 91.3", panel)
        self.assertIn("policy warning 90 / critical 99", panel)

    def test_config_change_record_is_labelled_as_the_operator_path(self):
        panel = reeval_panel(alert_with(reevaluated_on_config_change=CONFIG_CHANGE_RECORD))
        self.assertIn("On a policy change", panel)
        self.assertNotIn("At ingest", panel)
        self.assertIn("WARNING to INFO", panel)
        self.assertIn("4 Aug 2026", panel)
        self.assertIn("status firing to resolved", panel)
        self.assertIn("value 41.2", panel)
        self.assertIn("policy warning 80 / critical 95", panel)

    def test_both_records_render_distinguishably(self):
        panel = reeval_panel(
            alert_with(
                severity_reevaluated=INGEST_RECORD,
                reevaluated_on_config_change=CONFIG_CHANGE_RECORD,
            )
        )
        self.assertIn("At ingest", panel)
        self.assertIn("On a policy change", panel)
        self.assertIn("CRITICAL to WARNING", panel)
        self.assertIn("WARNING to INFO", panel)
        self.assertLess(panel.index("At ingest"), panel.index("On a policy change"))

    def test_no_record_renders_never_re_evaluated(self):
        self.assertEqual(reeval_panel(alert_with()), NEVER)

    def test_annotations_not_a_dict_renders_never_re_evaluated(self):
        alert = alert_with()
        alert.annotations = None
        self.assertEqual(reeval_panel(alert), NEVER)

    def test_malformed_json_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": "not json at all"}
        self.assertIn(UNREADABLE, reeval_panel(alert))

    def test_non_string_annotation_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": {"from": "critical"}}
        self.assertIn(UNREADABLE, reeval_panel(alert))

    def test_record_parsing_to_a_list_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": "[]"}
        self.assertIn(UNREADABLE, reeval_panel(alert))

    def test_record_parsing_to_a_number_renders_the_fallback(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": "3"}
        self.assertIn(UNREADABLE, reeval_panel(alert))

    def test_empty_record_renders_the_fallback(self):
        panel = reeval_panel(alert_with(severity_reevaluated={}))
        self.assertIn(UNREADABLE, panel)

    def test_record_without_value_or_thresholds_renders_what_it_has(self):
        panel = reeval_panel(alert_with(severity_reevaluated={"from": "critical", "to": "warning"}))
        self.assertIn("CRITICAL to WARNING.", panel)
        self.assertNotIn("value", panel)
        self.assertNotIn("policy", panel)

    def test_record_without_a_severity_change_still_renders_its_date(self):
        panel = reeval_panel(
            alert_with(reevaluated_on_config_change={"at": "2026-08-04T09:15:00+00:00"})
        )
        self.assertIn("on 4 Aug 2026.", panel)

    def test_unparseable_timestamp_is_dropped_not_raised(self):
        panel = reeval_panel(
            alert_with(reevaluated_on_config_change=dict(CONFIG_CHANGE_RECORD, at="last Tuesday"))
        )
        self.assertIn("WARNING to INFO", panel)
        self.assertNotIn(" on ", panel)

    def test_impossible_timestamp_is_dropped_not_raised(self):
        panel = reeval_panel(
            alert_with(
                reevaluated_on_config_change=dict(CONFIG_CHANGE_RECORD, at="2026-13-45T99:00:00")
            )
        )
        self.assertIn("WARNING to INFO", panel)
        self.assertNotIn(" on ", panel)

    def test_naive_timestamp_renders_without_conversion(self):
        panel = reeval_panel(
            alert_with(reevaluated_on_config_change=dict(CONFIG_CHANGE_RECORD, at="2026-08-04"))
        )
        self.assertIn("4 Aug 2026", panel)

    def test_thresholds_of_the_wrong_shape_drop_the_policy_clause(self):
        panel = reeval_panel(
            alert_with(severity_reevaluated=dict(INGEST_RECORD, thresholds="90/99"))
        )
        self.assertIn("CRITICAL to WARNING", panel)
        self.assertNotIn("policy", panel)

    def test_thresholds_holding_neither_thresholds_nor_allowlist_drop_the_clause(self):
        panel = reeval_panel(
            alert_with(severity_reevaluated=dict(INGEST_RECORD, thresholds={"enabled": True}))
        )
        self.assertNotIn("policy", panel)

    def test_listening_ports_renders_its_allowlist_not_thresholds(self):
        panel = reeval_panel(
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
        panel = reeval_panel(
            alert_with(
                severity_reevaluated=dict(INGEST_RECORD, thresholds={"allowlist": []}),
            )
        )
        self.assertIn("policy allows no ports", panel)

    def test_values_render_at_the_confirm_page_precision(self):
        panel = reeval_panel(
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

    def test_policy_values_from_config_are_escaped(self):
        panel = reeval_panel(
            alert_with(
                severity_reevaluated=dict(
                    INGEST_RECORD, thresholds={"allowlist": ["<script>alert(1)</script>"]}
                )
            )
        )
        self.assertNotIn("<script>", panel)
        self.assertIn("&lt;script&gt;", panel)


class AlertChangePageTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(
            username="ops", email="ops@example.com", password="pw"
        )
        self.client.login(username="ops", password="pw")

    def test_change_page_renders_the_panel(self):
        alert = alert_with(severity_reevaluated=INGEST_RECORD)
        response = self.client.get(reverse("admin:alerts_alert_change", args=[alert.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Re-evaluation", body)
        self.assertIn("CRITICAL to WARNING", body)
        self.assertIn("policy warning 90 / critical 99", body)

    def test_change_page_opens_on_an_unreadable_record(self):
        alert = alert_with()
        alert.annotations = {"severity_reevaluated": "not json at all"}
        alert.save()
        response = self.client.get(reverse("admin:alerts_alert_change", args=[alert.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(UNREADABLE, response.content.decode())
