"""Tests for checker management commands."""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from apps.checkers.checkers.base import CheckResult, CheckStatus


class CheckHealthCommandTests(TestCase):
    """Tests for the check_health management command."""

    def test_check_health_calls_run(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.check_health.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command("check_health", "cpu", stdout=StringIO())

        mock_checker.return_value.run.assert_called_once()


class RunCheckCommandTests(TestCase):
    """Tests for the run_check management command."""

    def test_run_check_calls_run(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.run_check.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command("run_check", "cpu", stdout=StringIO())

        mock_checker.return_value.run.assert_called_once()
