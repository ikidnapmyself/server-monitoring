"""Tests for the checker registry."""

from django.test import TestCase

from apps.checkers.checkers import (
    CHECKER_REGISTRY,
    CPUChecker,
    DiskChecker,
    MemoryChecker,
    NetworkChecker,
    ProcessChecker,
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
