"""Tests for Django system checks in the checkers app."""

import os
import time
from unittest.mock import MagicMock, mock_open, patch

from django.test import TestCase

from apps.checkers.checks import (
    check_base_dir_writable,
    check_cron_log_freshness,
    check_cron_log_size,
    check_crontab_configuration,
    check_database_connection,
    check_debug_mode,
    check_env_file_exists,
    check_notification_channels,
    check_pending_migrations,
    check_pipeline_status,
    check_required_env_vars,
    check_secret_key_strength,
)
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


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
        mock_result.stdout = "*/5 * * * * cd /path && uv run python manage.py run_pipeline --checks-only --json # server-maintanence health check"

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

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_run_pipeline_without_checks_only_warns(self, mock_is_testing):
        """W005 is raised when crontab has run_pipeline but not --checks-only."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "*/5 * * * * python manage.py run_pipeline # server-maintanence"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W005")
            self.assertIn("--checks-only", errors[0].hint)

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_crontab_check_server_maintanence_without_run_pipeline_warns(self, mock_is_testing):
        """W005 is raised when crontab has server-maintanence but not run_pipeline."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "* * * * * server-maintanence check"

        with patch("subprocess.run", return_value=mock_result):
            errors = check_crontab_configuration(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W005")


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
        sample_content = "DJANGO_DEBUG=1\nSTATSD_HOST=localhost\n"
        with patch("builtins.open", mock_open(read_data=sample_content)):
            with patch.dict("os.environ", {"DJANGO_DEBUG": "1"}, clear=False):
                env = os.environ.copy()
                env.pop("STATSD_HOST", None)
                with patch.dict("os.environ", env, clear=True):
                    errors = check_required_env_vars(app_configs=None)
                    warning_vars = [e.msg for e in errors]
                    self.assertTrue(any("STATSD_HOST" in msg for msg in warning_vars))
                    self.assertTrue(all(e.id == "checkers.I003" for e in errors))

    @patch("os.path.isfile", return_value=True)
    def test_required_env_vars_ok_when_all_set(self, mock_isfile):
        """Test that required env vars check passes when all vars are set."""
        sample_content = "DJANGO_DEBUG=1\nSTATSD_HOST=localhost\n"
        with patch("builtins.open", mock_open(read_data=sample_content)):
            with patch.dict(
                "os.environ",
                {"DJANGO_DEBUG": "1", "STATSD_HOST": "localhost"},
                clear=False,
            ):
                errors = check_required_env_vars(app_configs=None)
                self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=False)
    def test_required_env_vars_skips_when_no_sample(self, mock_isfile):
        """Test that required env vars check skips when .env.sample is missing."""
        errors = check_required_env_vars(app_configs=None)
        self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=True)
    def test_required_env_vars_oserror_reading_sample(self, mock_isfile):
        """Test graceful handling when .env.sample can't be read."""
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            errors = check_required_env_vars(app_configs=None)
            self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=True)
    def test_required_env_vars_skips_non_matching_lines(self, mock_isfile):
        """Test that non-variable lines in .env.sample are skipped."""
        sample_content = "# This is a comment\n\nlowercase=value\n"
        with patch("builtins.open", mock_open(read_data=sample_content)):
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


class PipelineChecksTests(TestCase):
    """Tests for pipeline system checks (definitions, notification channels)."""

    def test_pipeline_status_info_with_definitions(self):
        """Test pipeline status reports active/inactive counts."""
        PipelineDefinition.objects.create(name="pipeline-active", is_active=True)
        PipelineDefinition.objects.create(name="pipeline-inactive", is_active=False)
        errors = check_pipeline_status(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.I001")
        self.assertIn("2 pipeline", errors[0].msg)
        self.assertIn("1 active", errors[0].msg)

    def test_pipeline_status_info_with_none(self):
        """Test pipeline status reports zero definitions."""
        errors = check_pipeline_status(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.I001")
        self.assertIn("0 pipeline", errors[0].msg)

    def test_notification_channels_warns_when_none_active(self):
        """Test notification channels check warns when no active channels."""
        errors = check_notification_channels(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.W014")
        self.assertIn("No active", errors[0].msg)

    def test_notification_channels_warns_on_empty_config(self):
        """Test notification channels check warns on empty config."""
        NotificationChannel.objects.create(
            name="test-channel", driver="generic", config={}, is_active=True
        )
        errors = check_notification_channels(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.W014")
        self.assertIn("empty config", errors[0].msg)

    def test_pipeline_status_handles_exception(self):
        """Test pipeline status handles database errors gracefully."""
        with patch(
            "apps.orchestration.models.PipelineDefinition.objects.all",
            side_effect=Exception("DB error"),
        ):
            errors = check_pipeline_status(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertIn("Cannot check", errors[0].msg)

    def test_notification_channels_handles_exception(self):
        """Test notification channels handles database errors gracefully."""
        with patch(
            "apps.notify.models.NotificationChannel.objects.filter",
            side_effect=Exception("DB error"),
        ):
            errors = check_notification_channels(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertIn("Cannot check", errors[0].msg)

    def test_notification_channels_ok_when_valid(self):
        """Test notification channels check passes with valid config."""
        NotificationChannel.objects.create(
            name="test-channel",
            driver="generic",
            config={"url": "https://example.com/webhook"},
            is_active=True,
        )
        errors = check_notification_channels(app_configs=None)
        # Should have no W014 warnings
        w014_errors = [e for e in errors if e.id == "checkers.W014"]
        self.assertEqual(w014_errors, [])


class CronLogChecksTests(TestCase):
    """Tests for cron log system checks (freshness, size)."""

    @patch("apps.checkers.checks._is_testing", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run")
    @patch("os.path.getmtime")
    def test_cron_log_freshness_warns_when_stale(
        self, mock_getmtime, mock_subprocess, mock_isfile, mock_is_testing
    ):
        """Test that cron log freshness check warns when log is stale."""
        mock_getmtime.return_value = time.time() - 7200  # 2 hours ago
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "* * * * * server-maintanence check"
        mock_subprocess.return_value = mock_result
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.W015")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run")
    @patch("os.path.getmtime")
    def test_cron_log_freshness_ok_when_recent(
        self, mock_getmtime, mock_subprocess, mock_isfile, mock_is_testing
    ):
        """Test that cron log freshness check passes when log is recent."""
        mock_getmtime.return_value = time.time() - 60  # 1 minute ago
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "* * * * * server-maintanence check"
        mock_subprocess.return_value = mock_result
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    @patch("os.path.isfile", return_value=False)
    def test_cron_log_freshness_skips_when_no_log(self, mock_isfile, mock_is_testing):
        """Test that cron log freshness check skips when no log file."""
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(errors, [])

    def test_cron_log_freshness_skips_in_tests(self):
        """Test that cron log freshness check is skipped during tests."""
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=True)
    @patch("os.path.getsize", return_value=60 * 1024 * 1024)
    def test_cron_log_size_warns_when_large(self, mock_getsize, mock_isfile):
        """Test that cron log size check warns when log is too large."""
        errors = check_cron_log_size(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.W016")

    @patch("os.path.isfile", return_value=True)
    @patch("os.path.getsize", return_value=1024)
    def test_cron_log_size_ok_when_small(self, mock_getsize, mock_isfile):
        """Test that cron log size check passes when log is small."""
        errors = check_cron_log_size(app_configs=None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run")
    def test_cron_log_freshness_skips_when_cron_not_configured(
        self, mock_subprocess, mock_isfile, mock_is_testing
    ):
        """Test freshness check skips when cron is not configured for this project."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "* * * * * /some/other/job"  # No server-maintanence
        mock_subprocess.return_value = mock_result
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run")
    @patch("os.path.getmtime", side_effect=OSError("Permission denied"))
    def test_cron_log_freshness_handles_oserror(
        self, mock_getmtime, mock_subprocess, mock_isfile, mock_is_testing
    ):
        """Test freshness check handles OSError on getmtime."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "* * * * * server-maintanence check"
        mock_subprocess.return_value = mock_result
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run", side_effect=Exception("crontab failed"))
    def test_cron_log_freshness_handles_subprocess_exception(
        self, mock_subprocess, mock_isfile, mock_is_testing
    ):
        """Test freshness check handles subprocess exception gracefully."""
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=True)
    @patch("os.path.getsize", side_effect=OSError("Permission denied"))
    def test_cron_log_size_handles_oserror(self, mock_getsize, mock_isfile):
        """Test size check handles OSError gracefully."""
        errors = check_cron_log_size(app_configs=None)
        self.assertEqual(errors, [])

    @patch("os.path.isfile", return_value=False)
    def test_cron_log_size_skips_when_no_log(self, mock_isfile):
        """Test that cron log size check skips when no log file."""
        errors = check_cron_log_size(app_configs=None)
        self.assertEqual(errors, [])
