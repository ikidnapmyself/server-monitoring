"""Tests for Django system checks in the checkers app."""

import os
from unittest.mock import MagicMock, mock_open, patch

from django.test import TestCase

from apps.checkers.checks import (
    check_base_dir_writable,
    check_crontab_configuration,
    check_database_connection,
    check_debug_mode,
    check_env_file_exists,
    check_pending_migrations,
    check_required_env_vars,
    check_secret_key_strength,
)


class SystemChecksTests(TestCase):
    """Tests for Django system checks (database, migrations, crontab)."""

    def test_database_check_success(self):
        """Test that database check passes when database is accessible."""
        errors = check_database_connection(app_configs=None)
        # Should return empty list (no errors) if database is configured
        self.assertEqual(errors, [])

    def test_database_check_failure(self):
        """Test that database check fails with invalid database config."""
        with patch("django.db.connections") as mock_connections:
            mock_conn = MagicMock()
            mock_conn.ensure_connection.side_effect = Exception("Connection refused")
            mock_connections.__iter__ = lambda self: iter(["default"])
            mock_connections.__getitem__ = lambda self, key: mock_conn

            errors = check_database_connection(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.E001")

    def test_migrations_check_no_pending(self):
        """Test that migrations check passes when all migrations are applied."""
        with patch("django.db.connections") as mock_connections:
            mock_conn = MagicMock()
            mock_connections.__iter__ = lambda self: iter(["default"])
            mock_connections.__getitem__ = lambda self, key: mock_conn

            with patch("django.db.migrations.executor.MigrationExecutor") as mock_executor_class:
                mock_executor = MagicMock()
                mock_executor.migration_plan.return_value = []  # No pending migrations
                mock_executor_class.return_value = mock_executor

                errors = check_pending_migrations(app_configs=None)
                self.assertEqual(errors, [])

    def test_migrations_check_pending(self):
        """Test that migrations check warns when there are pending migrations."""
        with patch("django.db.connections") as mock_connections:
            mock_conn = MagicMock()
            mock_connections.__iter__ = lambda self: iter(["default"])
            mock_connections.__getitem__ = lambda self, key: mock_conn

            with patch("django.db.migrations.executor.MigrationExecutor") as mock_executor_class:
                # Create mock migration
                mock_migration = MagicMock()
                mock_migration.app_label = "myapp"
                mock_migration.name = "0001_initial"

                mock_executor = MagicMock()
                mock_executor.migration_plan.return_value = [(mock_migration, False)]
                mock_executor_class.return_value = mock_executor

                errors = check_pending_migrations(app_configs=None)
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0].id, "checkers.W001")
                self.assertIn("pending migration", errors[0].msg.lower())

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_with_cron_configured(self, mock_is_testing):
        """Test that crontab check passes when cron job is configured."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "*/5 * * * * cd /path && uv run python manage.py check_and_alert --json # server-maintanence health check"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_no_cron_job(self, mock_is_testing):
        """Test that crontab check warns when cron job is missing."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "0 0 * * * /some/other/job"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W004")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_no_crontab(self, mock_is_testing):
        """Test that crontab check warns when no crontab exists."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "no crontab for user"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W002")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_command_not_found(self, mock_is_testing):
        """Test that crontab check handles missing crontab command."""
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W006")


class SecurityChecksTests(TestCase):
    """Tests for security system checks (debug mode, secret key)."""

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_debug_mode_warns_when_true(self, mock_is_testing):
        """Test that debug mode check warns when DEBUG=True."""
        with self.settings(DEBUG=True):
            errors = check_debug_mode(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W010")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_debug_mode_ok_when_false(self, mock_is_testing):
        """Test that debug mode check passes when DEBUG=False."""
        with self.settings(DEBUG=False):
            errors = check_debug_mode(app_configs=None)
            self.assertEqual(errors, [])

    def test_debug_mode_skipped_in_tests(self):
        """Test that debug mode check is skipped during tests."""
        errors = check_debug_mode(app_configs=None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_secret_key_warns_when_short(self, mock_is_testing):
        """Test that secret key check warns when key is too short."""
        with self.settings(SECRET_KEY="short"):
            errors = check_secret_key_strength(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W011")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_secret_key_warns_when_insecure(self, mock_is_testing):
        """Test that secret key check warns when key contains 'insecure'."""
        with self.settings(SECRET_KEY="django-insecure-" + "x" * 50):
            errors = check_secret_key_strength(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W011")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_secret_key_ok_when_strong(self, mock_is_testing):
        """Test that secret key check passes with a strong key."""
        with self.settings(SECRET_KEY="a" * 50):
            errors = check_secret_key_strength(app_configs=None)
            self.assertEqual(errors, [])

    def test_secret_key_skipped_in_tests(self):
        """Test that secret key check is skipped during tests."""
        errors = check_secret_key_strength(app_configs=None)
        self.assertEqual(errors, [])


class EnvironmentChecksTests(TestCase):
    """Tests for environment system checks (.env, env vars, writable dir)."""

    @patch("os.path.isfile", return_value=False)
    def test_env_file_warns_when_missing(self, mock_isfile):
        """Test that .env check warns when file is missing."""
        errors = check_env_file_exists(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.W012")

    @patch("os.path.isfile", return_value=True)
    def test_env_file_ok_when_present(self, mock_isfile):
        """Test that .env check passes when file exists."""
        errors = check_env_file_exists(app_configs=None)
        self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=True)
    def test_required_env_vars_warns_on_missing(self, mock_isfile):
        """Test that required env vars check warns on missing variables."""
        sample_content = "DJANGO_DEBUG=1\nOPENAI_API_KEY=\n"
        with patch("builtins.open", mock_open(read_data=sample_content)):
            with patch.dict("os.environ", {"DJANGO_DEBUG": "1"}, clear=False):
                # Remove OPENAI_API_KEY if it exists
                env = os.environ.copy()
                env.pop("OPENAI_API_KEY", None)
                with patch.dict("os.environ", env, clear=True):
                    errors = check_required_env_vars(app_configs=None)
                    warning_vars = [e.msg for e in errors]
                    self.assertTrue(any("OPENAI_API_KEY" in msg for msg in warning_vars))
                    self.assertTrue(all(e.id == "checkers.W013" for e in errors))

    @patch("os.path.isfile", return_value=True)
    def test_required_env_vars_ok_when_all_set(self, mock_isfile):
        """Test that required env vars check passes when all vars are set."""
        sample_content = "DJANGO_DEBUG=1\nOPENAI_API_KEY=\n"
        with patch("builtins.open", mock_open(read_data=sample_content)):
            with patch.dict(
                "os.environ",
                {"DJANGO_DEBUG": "1", "OPENAI_API_KEY": "sk-test"},
                clear=False,
            ):
                errors = check_required_env_vars(app_configs=None)
                self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=False)
    def test_required_env_vars_skips_when_no_sample(self, mock_isfile):
        """Test that required env vars check skips when .env.sample is missing."""
        errors = check_required_env_vars(app_configs=None)
        self.assertEqual(errors, [])

    @patch("os.access", return_value=True)
    def test_base_dir_writable_ok(self, mock_access):
        """Test that base dir writable check passes when directory is writable."""
        errors = check_base_dir_writable(app_configs=None)
        self.assertEqual(errors, [])

    @patch("os.access", return_value=False)
    def test_base_dir_not_writable_warns(self, mock_access):
        """Test that base dir writable check warns when not writable."""
        errors = check_base_dir_writable(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.W017")
