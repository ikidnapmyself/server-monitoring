"""Tests for the aliases Django system check."""

from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.checks import _aliases_file_exists, check_aliases_configured


class CheckAliasesConfiguredTests(TestCase):
    """Tests for the check_aliases_configured system check."""

    @override_settings(DEBUG=False)
    def test_skips_in_production(self):
        """Check should return no warnings when DEBUG=False."""
        result = check_aliases_configured(None)
        assert result == []

    @override_settings(DEBUG=True)
    def test_skips_during_tests(self):
        """Check should return no warnings when running under pytest."""
        result = check_aliases_configured(None)
        assert result == []

    @override_settings(DEBUG=True)
    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_warns_when_aliases_file_missing(self, _mock_testing):
        """Check should warn when bin/aliases.sh does not exist."""
        with patch("apps.checkers.checks._aliases_file_exists", return_value=False):
            result = check_aliases_configured(None)
        assert len(result) == 1
        assert result[0].id == "checkers.W009"
        assert "aliases" in result[0].msg.lower()

    @override_settings(DEBUG=True)
    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_no_warning_when_aliases_file_exists(self, _mock_testing):
        """Check should return no warnings when bin/aliases.sh exists."""
        with patch("apps.checkers.checks._aliases_file_exists", return_value=True):
            result = check_aliases_configured(None)
        assert result == []


class AliasesFileExistsTests(TestCase):
    """Tests for the _aliases_file_exists helper."""

    @override_settings(BASE_DIR=None)
    def test_returns_true_when_base_dir_is_none(self):
        """Should return True (don't warn) when BASE_DIR is not set."""
        assert _aliases_file_exists() is True

    def test_returns_true_when_file_exists(self):
        """Should return True when bin/aliases.sh exists."""
        with patch("os.path.isfile", return_value=True):
            assert _aliases_file_exists() is True

    def test_returns_false_when_file_missing(self):
        """Should return False when bin/aliases.sh does not exist."""
        with patch("os.path.isfile", return_value=False):
            assert _aliases_file_exists() is False
