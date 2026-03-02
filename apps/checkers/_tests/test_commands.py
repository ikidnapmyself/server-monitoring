"""Tests for checker management commands."""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.checks import CheckMessage, Error
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

    def test_run_check_passes_samples_to_cpu(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu_percent": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.run_check.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command(
                "run_check",
                "cpu",
                "--samples",
                "3",
                "--sample-interval",
                "0.5",
                stdout=StringIO(),
            )

        mock_checker.assert_called_once_with(samples=3, sample_interval=0.5)

    def test_run_check_cpu_default_no_extra_kwargs(self):
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"cpu_percent": 10},
            checker_name="cpu",
        )

        with patch.dict(
            "apps.checkers.management.commands.run_check.CHECKER_REGISTRY",
            {"cpu": mock_checker},
            clear=True,
        ):
            call_command("run_check", "cpu", stdout=StringIO())

        mock_checker.assert_called_once_with()


class PreflightCommandTests(TestCase):
    """Tests for the preflight management command."""

    def test_preflight_runs_all_groups(self):
        out = StringIO()
        call_command("preflight", stdout=out)
        output = out.getvalue()
        self.assertIn("Preflight Check", output)
        self.assertIn("Summary", output)

    def test_preflight_only_filter(self):
        out = StringIO()
        call_command("preflight", "--only", "security", stdout=out)
        output = out.getvalue()
        self.assertIn("Security", output)
        # Should not contain other groups
        self.assertNotIn("Pipeline", output)

    def test_preflight_json_output(self):
        import json

        out = StringIO()
        call_command("preflight", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertIn("groups", data)
        self.assertIn("summary", data)

    def test_preflight_json_with_filter(self):
        import json

        out = StringIO()
        call_command("preflight", "--json", "--only", "security", stdout=out)
        data = json.loads(out.getvalue())
        self.assertIn("security", data["groups"])
        self.assertNotIn("pipeline", data["groups"])


class PreflightDisplayTests(TestCase):
    """Tests for preflight command display edge cases."""

    def test_preflight_displays_error_level(self):
        """Test that error-level checks are displayed with ERR prefix."""
        mock_error = Error("Test error message", hint="Fix this", id="test.E001")
        with patch(
            "apps.checkers.management.commands.preflight.run_checks",
            return_value=[mock_error],
        ):
            out = StringIO()
            call_command("preflight", "--only", "security", stdout=out)
            output = out.getvalue()
            self.assertIn("ERR", output)
            self.assertIn("Test error message", output)

    def test_preflight_displays_ok_level(self):
        """Test that non-error/warning/info checks show OK."""
        # A plain CheckMessage (not Error/Warning/Info) should show as OK
        mock_check = CheckMessage(0, "All good", id="test.C001")
        with patch(
            "apps.checkers.management.commands.preflight.run_checks",
            return_value=[mock_check],
        ):
            out = StringIO()
            call_command("preflight", "--only", "security", stdout=out)
            output = out.getvalue()
            self.assertIn("OK", output)

    def test_preflight_no_checks_registered(self):
        """Test display when a tag group has no checks."""
        with patch(
            "apps.checkers.management.commands.preflight.run_checks",
            return_value=[],
        ):
            out = StringIO()
            call_command("preflight", "--only", "security", stdout=out)
            output = out.getvalue()
            self.assertIn("no checks registered", output)

    def test_preflight_error_summary_style(self):
        """Test that summary uses error style when errors exist."""
        mock_error = Error("Critical failure", id="test.E001")
        with patch(
            "apps.checkers.management.commands.preflight.run_checks",
            return_value=[mock_error],
        ):
            out = StringIO()
            call_command("preflight", "--only", "security", stdout=out)
            output = out.getvalue()
            self.assertIn("1 error(s)", output)
