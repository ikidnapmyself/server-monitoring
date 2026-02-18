"""Tests for the aliases Django system check."""

from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.checks import check_aliases_configured


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
