"""Tests for installation state checks."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.status.installation_checks import (
    _check_crontab,
    _is_writable,
    _path_exists,
    run,
)


class InstallationChecksTests(TestCase):
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    def test_aliases_missing_in_dev(self, mock_exists):
        mock_exists.side_effect = lambda p: "aliases" not in str(p)
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("aliases" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    def test_precommit_missing_in_dev(self, mock_exists):
        mock_exists.side_effect = lambda p: "pre-commit" not in str(p)
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("pre-commit" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @patch("apps.checkers.status.installation_checks._check_crontab")
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_cron_missing_in_prod(self, mock_writable, mock_cron, mock_exists):
        mock_exists.return_value = True
        mock_cron.return_value = False
        mock_writable.return_value = True
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("cron" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_logs_dir_not_writable(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = False
        results = run(base_dir=Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("logs" in r.message.lower() for r in errors))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @override_settings(
        LOGS_DIR=Path("/fake/logs"),
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": Path("/fake/db.sqlite3"),
            }
        },
    )
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_db_file_not_writable(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.side_effect = lambda p: "db" not in str(p)
        results = run(base_dir=Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("database" in r.message.lower() for r in errors))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_all_ok_in_dev(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = True
        results = run(base_dir=Path("/fake"))
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)

    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @patch("apps.checkers.status.installation_checks._check_crontab")
    @patch("apps.checkers.status.installation_checks._is_writable")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    def test_all_ok_in_prod(self, mock_writable, mock_cron, mock_exists):
        mock_exists.return_value = True
        mock_cron.return_value = True
        mock_writable.return_value = True
        results = run(base_dir=Path("/fake"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")
        self.assertIn("consistent", results[0].message.lower())

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @override_settings(
        LOGS_DIR=Path("/fake/logs"),
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "mydb",
            }
        },
    )
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_non_sqlite_db_skips_file_check(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = True
        results = run(base_dir=Path("/fake"))
        self.assertFalse(any("database" in r.message.lower() for r in results))


class HelperFunctionTests(TestCase):
    """Tests for the I/O helper functions."""

    def test_path_exists_true(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertTrue(_path_exists(Path(f.name)))

    def test_path_exists_false(self):
        self.assertFalse(_path_exists(Path("/nonexistent/path")))

    def test_is_writable_true(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertTrue(_is_writable(Path(f.name)))

    def test_is_writable_false(self):
        self.assertFalse(_is_writable(Path("/nonexistent/path")))

    @patch("apps.checkers.status.installation_checks.subprocess.run")
    def test_check_crontab_finds_project(self, mock_run):
        mock_run.return_value.stdout = "* * * * * cd /opt/server-maintanence && manage.py"
        self.assertTrue(_check_crontab())

    @patch("apps.checkers.status.installation_checks.subprocess.run")
    def test_check_crontab_no_match(self, mock_run):
        mock_run.return_value.stdout = "* * * * * some_other_cron_job"
        self.assertFalse(_check_crontab())

    @patch(
        "apps.checkers.status.installation_checks.subprocess.run",
        side_effect=FileNotFoundError,
    )
    def test_check_crontab_no_crontab_command(self, _mock_run):
        self.assertFalse(_check_crontab())
