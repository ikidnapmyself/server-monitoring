"""Tests for checker alert identity helpers."""

import socket

from django.test import SimpleTestCase, override_settings

from apps.alerts.identity import checker_fingerprint, local_instance_id


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
