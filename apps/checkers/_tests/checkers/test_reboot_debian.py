"""Tests for the Debian reboot-required checker."""

from unittest.mock import patch

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


class RebootDebianCheckerPlatformTests(TestCase):
    """Platform gating tests."""

    def _get_checker(self):
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        return RebootDebianChecker()

    @patch("apps.checkers.checkers.reboot_debian.sys")
    def test_skipped_on_macos(self, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "darwin"
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["platform"], "darwin")
        self.assertEqual(result.metrics["reboot_required"], False)

    @patch("apps.checkers.checkers.reboot_debian.sys")
    def test_skipped_on_windows(self, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "win32"
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["platform"], "win32")
