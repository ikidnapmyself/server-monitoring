"""Tests for the checker registry and enablement system."""

from django.test import TestCase
from django.test.utils import override_settings

from apps.checkers.checkers import (
    CHECKER_REGISTRY,
    CPUChecker,
    DiskChecker,
    MemoryChecker,
    NetworkChecker,
    ProcessChecker,
    get_enabled_checkers,
    is_checker_enabled,
)


class CheckerRegistryTests(TestCase):
    """Tests for the checker registry."""

    def test_all_checkers_in_registry(self):
        self.assertIn("cpu", CHECKER_REGISTRY)
        self.assertIn("memory", CHECKER_REGISTRY)
        self.assertIn("disk", CHECKER_REGISTRY)
        self.assertIn("network", CHECKER_REGISTRY)
        self.assertIn("process", CHECKER_REGISTRY)

    def test_registry_returns_checker_classes(self):
        self.assertEqual(CHECKER_REGISTRY["cpu"], CPUChecker)
        self.assertEqual(CHECKER_REGISTRY["memory"], MemoryChecker)
        self.assertEqual(CHECKER_REGISTRY["disk"], DiskChecker)
        self.assertEqual(CHECKER_REGISTRY["network"], NetworkChecker)
        self.assertEqual(CHECKER_REGISTRY["process"], ProcessChecker)


class CheckerEnablementTests(TestCase):
    """Tests for checker enable/disable settings."""

    @override_settings(CHECKERS_SKIP_ALL=True, CHECKERS_SKIP=[])
    def test_skip_all_disables_every_checker(self):
        for name in CHECKER_REGISTRY.keys():
            self.assertFalse(is_checker_enabled(name))

        self.assertEqual(get_enabled_checkers(), {})

    @override_settings(CHECKERS_SKIP_ALL=False, CHECKERS_SKIP=["network", "process"])
    def test_skip_list_disables_only_selected_checkers(self):
        self.assertFalse(is_checker_enabled("network"))
        self.assertFalse(is_checker_enabled("process"))
        self.assertTrue(is_checker_enabled("cpu"))
        self.assertTrue(is_checker_enabled("memory"))

        enabled = get_enabled_checkers()
        self.assertIn("cpu", enabled)
        self.assertIn("memory", enabled)
        self.assertNotIn("network", enabled)
        self.assertNotIn("process", enabled)
