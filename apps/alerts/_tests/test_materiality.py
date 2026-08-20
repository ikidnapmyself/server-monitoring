"""Tests for the shared materiality predicate behind the fan-out change gate."""

from django.test import SimpleTestCase

from apps.alerts.materiality import is_material_change


class MaterialityTests(SimpleTestCase):
    def test_unchanged_is_not_material(self):
        self.assertFalse(
            is_material_change(
                old_severity="warning",
                new_severity="warning",
                old_status="firing",
                new_status="firing",
                old_key="22",
                new_key="22",
            )
        )

    def test_severity_escalation_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning",
                new_severity="critical",
                old_status="firing",
                new_status="firing",
                old_key="",
                new_key="",
            )
        )

    def test_severity_de_escalation_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="critical",
                new_severity="warning",
                old_status="firing",
                new_status="firing",
                old_key="",
                new_key="",
            )
        )

    def test_resolution_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning",
                new_severity="warning",
                old_status="firing",
                new_status="resolved",
                old_key="",
                new_key="",
            )
        )

    def test_refire_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning",
                new_severity="warning",
                old_status="resolved",
                new_status="firing",
                old_key="",
                new_key="",
            )
        )

    def test_context_key_change_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning",
                new_severity="warning",
                old_status="firing",
                new_status="firing",
                old_key="22",
                new_key="22,8080",
            )
        )

    def test_namespaced_key_going_clean_is_material(self):
        """``"listening_ports:"`` is a clean scan, not "no key" — the drop is news."""
        self.assertTrue(
            is_material_change(
                old_severity="warning",
                new_severity="warning",
                old_status="firing",
                new_status="firing",
                old_key="listening_ports:22,8080",
                new_key="listening_ports:",
            )
        )

    def test_unchanged_namespaced_key_is_not_material(self):
        self.assertFalse(
            is_material_change(
                old_severity="warning",
                new_severity="warning",
                old_status="firing",
                new_status="firing",
                old_key="listening_ports:22",
                new_key="listening_ports:22",
            )
        )

    def test_none_and_empty_key_are_the_same_absence(self):
        """A pre-gate row can hold ``None``; that must not read as a change."""
        self.assertFalse(
            is_material_change(
                old_severity="warning",
                new_severity="warning",
                old_status="firing",
                new_status="firing",
                old_key=None,
                new_key="",
            )
        )
