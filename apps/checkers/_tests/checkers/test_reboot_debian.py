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

    @patch("apps.checkers.checkers.reboot_debian.sys")
    @patch("apps.checkers.checkers.reboot_debian._is_debian_family")
    def test_skipped_on_non_debian_linux(self, mock_distro, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "linux"
        mock_distro.return_value = (False, "fedora")
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Debian-family", result.message)
        self.assertIn("fedora", result.message)
        self.assertEqual(result.metrics["distro_id"], "fedora")

    @patch("apps.checkers.checkers.reboot_debian.sys")
    @patch("apps.checkers.checkers.reboot_debian._is_debian_family")
    def test_skipped_when_os_release_undetected(self, mock_distro, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "linux"
        mock_distro.return_value = (False, "")
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("cannot determine distro", result.message)
        self.assertEqual(result.metrics["distro_id"], "")


class RebootDebianCheckerStatusTests(TestCase):
    """Tests for the OK / WARNING result paths."""

    def _get_checker(self):
        from apps.checkers.checkers.reboot_debian import RebootDebianChecker

        return RebootDebianChecker()

    @patch("apps.checkers.checkers.reboot_debian.sys")
    @patch("apps.checkers.checkers.reboot_debian._is_debian_family")
    @patch("apps.checkers.checkers.reboot_debian._flag_present")
    def test_ok_when_flag_absent(self, mock_flag, mock_distro, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "linux"
        mock_distro.return_value = (True, "ubuntu")
        mock_flag.return_value = False
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("No reboot required", result.message)
        self.assertEqual(result.metrics["reboot_required"], False)
        self.assertEqual(result.metrics["distro_id"], "ubuntu")

    @patch("apps.checkers.checkers.reboot_debian.sys")
    @patch("apps.checkers.checkers.reboot_debian._is_debian_family")
    @patch("apps.checkers.checkers.reboot_debian._flag_present")
    @patch("apps.checkers.checkers.reboot_debian._read_pkgs")
    def test_warning_when_flag_present_no_pkgs(self, mock_pkgs, mock_flag, mock_distro, mock_sys):
        from apps.checkers.checkers.base import CheckStatus

        mock_sys.platform = "linux"
        mock_distro.return_value = (True, "debian")
        mock_flag.return_value = True
        mock_pkgs.return_value = []
        result = self._get_checker().check()

        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.message, "Reboot required")
        self.assertEqual(result.metrics["reboot_required"], True)
        self.assertEqual(result.metrics["pending_packages"], [])
        self.assertEqual(result.metrics["pending_package_count"], 0)


class IsDebianFamilyTests(TestCase):
    """Tests for the _is_debian_family() helper."""

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_missing_os_release(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = False
        is_debian, distro_id = _is_debian_family()

        self.assertFalse(is_debian)
        self.assertEqual(distro_id, "")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_unreadable_os_release(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("permission denied")
        is_debian, distro_id = _is_debian_family()

        self.assertFalse(is_debian)
        self.assertEqual(distro_id, "")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_debian_via_id(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'ID=debian\nVERSION="12 (bookworm)"\n'
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "debian")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_ubuntu_via_id(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "ID=ubuntu\nID_LIKE=debian\n"
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "ubuntu")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_derivative_via_id_like(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'ID=linuxmint\nID_LIKE="ubuntu debian"\n'
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "linuxmint")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_non_debian(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'ID=fedora\nID_LIKE="rhel"\n'
        is_debian, distro_id = _is_debian_family()

        self.assertFalse(is_debian)
        self.assertEqual(distro_id, "fedora")

    @patch("apps.checkers.checkers.reboot_debian.OS_RELEASE")
    def test_handles_quoted_values_and_blank_lines(self, mock_path):
        from apps.checkers.checkers.reboot_debian import _is_debian_family

        mock_path.exists.return_value = True
        mock_path.read_text.return_value = (
            "\n"
            'NAME="Ubuntu"\n'
            "ID='ubuntu'\n"
            "# comment-style line without =\n"
            'PRETTY_NAME="Ubuntu 22.04"\n'
        )
        is_debian, distro_id = _is_debian_family()

        self.assertTrue(is_debian)
        self.assertEqual(distro_id, "ubuntu")
