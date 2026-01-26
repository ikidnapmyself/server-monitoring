"""Tests for Django system checks in the checkers app."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checks import (
    check_crontab_configuration,
    check_database_connection,
    check_pending_migrations,
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
