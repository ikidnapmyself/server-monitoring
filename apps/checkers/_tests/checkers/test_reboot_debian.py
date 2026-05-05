"""Tests for the Debian reboot-required checker."""

from django.test import TestCase


class RebootDebianRegistryTests(TestCase):
    """Tests that the checker is wired into the registry."""

    def test_registered_in_checker_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        self.assertIs(CHECKER_REGISTRY["reboot_debian"], RebootDebianChecker)

    def test_exported_from_package(self):
        from apps.checkers.checkers import RebootDebianChecker

        self.assertEqual(RebootDebianChecker.name, "reboot_debian")
