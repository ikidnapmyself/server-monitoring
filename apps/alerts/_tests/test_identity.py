"""Tests for checker alert identity helpers."""

import socket

from django.test import SimpleTestCase, override_settings

from apps.alerts.identity import alert_subject, checker_fingerprint, local_instance_id


class CheckerFingerprintTests(SimpleTestCase):
    def test_format_is_readable_and_prefixed(self):
        self.assertEqual(checker_fingerprint("web-01-a3f2", "cpu"), "check:web-01-a3f2:cpu")

    def test_instance_id_separates_same_checker_on_two_machines(self):
        self.assertNotEqual(
            checker_fingerprint("a", "cpu"),
            checker_fingerprint("b", "cpu"),
        )

    def test_underscored_checker_names_survive(self):
        self.assertEqual(checker_fingerprint("n1", "disk_macos"), "check:n1:disk_macos")


class LocalInstanceIdTests(SimpleTestCase):
    @override_settings(INSTANCE_ID="configured-id")
    def test_prefers_configured_instance_id(self):
        self.assertEqual(local_instance_id(), "configured-id")

    @override_settings(INSTANCE_ID="")
    def test_falls_back_to_hostname(self):
        self.assertEqual(local_instance_id(), socket.gethostname())


class AlertSubjectTests(SimpleTestCase):
    def test_checker_label_names_the_subject(self):
        self.assertEqual(
            alert_subject("cluster", "CPU Check Alert", {"checker": "cpu"}), "checker:cpu"
        )

    def test_without_checker_label_source_and_name(self):
        self.assertEqual(
            alert_subject("alertmanager", "HighCPU", {"instance": "web-1"}),
            "alertmanager:HighCPU",
        )

    def test_blank_checker_label_falls_back(self):
        self.assertEqual(alert_subject("grafana", "X", {"checker": ""}), "grafana:X")

    def test_non_string_checker_label_falls_back(self):
        self.assertEqual(alert_subject("grafana", "X", {"checker": ["cpu"]}), "grafana:X")

    def test_non_dict_labels_do_not_raise(self):
        self.assertEqual(alert_subject("generic", "X", "pwned"), "generic:X")

    def test_capped_at_column_length(self):
        self.assertEqual(len(alert_subject("generic", "x" * 400, {})), 255)
