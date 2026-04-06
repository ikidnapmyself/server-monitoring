"""Tests for all preflight check functions."""

import os
import subprocess
import tempfile
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.checkers.preflight import CheckResult
from apps.checkers.preflight.checks import (
    _check_crontab,
    _is_writable,
    _parse_env_keys,
    _parse_sample_keys,
    _parse_settings_env_refs,
    _path_exists,
    _read_file,
    check_allowed_hosts,
    check_celery_eager,
    check_cluster_coherence,
    check_database_connection,
    check_debug_mode,
    check_deployment,
    check_disk_space,
    check_django_system,
    check_env_consistency,
    check_env_file_exists,
    check_env_file_permissions,
    check_installation_state,
    check_metrics_config,
    check_pending_migrations,
    check_pipeline_state,
    check_project_writable,
    check_python_version,
    check_secret_key_strength,
    check_uv_installed,
    check_venv_exists,
    run_all,
)

# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class ReadFileTests(TestCase):
    def test_reads_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("FOO=bar\n")
            f.flush()
            result = _read_file(Path(f.name))
        self.assertEqual(result, "FOO=bar\n")

    def test_returns_none_for_missing_file(self):
        result = _read_file(Path("/nonexistent/path/.env"))
        self.assertIsNone(result)


class PathExistsTests(TestCase):
    def test_true_for_existing(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertTrue(_path_exists(Path(f.name)))

    def test_false_for_missing(self):
        self.assertFalse(_path_exists(Path("/nonexistent/path")))


class IsWritableTests(TestCase):
    def test_true_for_writable(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertTrue(_is_writable(Path(f.name)))

    def test_false_for_missing(self):
        self.assertFalse(_is_writable(Path("/nonexistent/path")))


class CheckCrontabTests(TestCase):
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_finds_project(self, mock_run):
        mock_run.return_value.stdout = "* * * * * cd /opt/server-maintanence && manage.py"
        self.assertTrue(_check_crontab())

    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_finds_manage_py(self, mock_run):
        mock_run.return_value.stdout = "* * * * * manage.py check_health"
        self.assertTrue(_check_crontab())

    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_no_match(self, mock_run):
        mock_run.return_value.stdout = "* * * * * some_other_cron_job"
        self.assertFalse(_check_crontab())

    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=FileNotFoundError,
    )
    def test_no_crontab_command(self, _mock_run):
        self.assertFalse(_check_crontab())

    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["crontab", "-l"], 5),
    )
    def test_timeout(self, _mock_run):
        self.assertFalse(_check_crontab())

    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=OSError("os error"),
    )
    def test_os_error(self, _mock_run):
        self.assertFalse(_check_crontab())


class ParseEnvKeysTests(TestCase):
    def test_parses_simple_keys(self):
        self.assertEqual(_parse_env_keys("FOO=bar\nBAZ=qux\n"), {"FOO", "BAZ"})

    def test_ignores_comments(self):
        self.assertEqual(_parse_env_keys("# comment\nFOO=bar\n# BAZ=qux\n"), {"FOO"})

    def test_ignores_blank_lines(self):
        self.assertEqual(_parse_env_keys("\nFOO=bar\n\n\nBAZ=qux\n"), {"FOO", "BAZ"})

    def test_handles_values_with_equals(self):
        self.assertEqual(_parse_env_keys("URL=https://ex.com?a=1&b=2\n"), {"URL"})

    def test_empty_value(self):
        self.assertEqual(_parse_env_keys("FOO=\n"), {"FOO"})

    def test_empty_content(self):
        self.assertEqual(_parse_env_keys(""), set())

    def test_line_without_equals(self):
        self.assertEqual(_parse_env_keys("FOO=bar\nno_equals\nBAZ=qux\n"), {"FOO", "BAZ"})

    def test_empty_key_before_equals(self):
        self.assertEqual(_parse_env_keys("=value\nFOO=bar\n"), {"FOO"})


class ParseSampleKeysTests(TestCase):
    def test_parses_active_and_commented_keys(self):
        content = "FOO=bar\n# BAZ=qux\n# pure comment no equals\n"
        active, commented = _parse_sample_keys(content)
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, {"BAZ"})

    def test_double_hash_ignored(self):
        active, commented = _parse_sample_keys("## heading\nFOO=bar\n")
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, set())

    def test_commented_with_space(self):
        active, commented = _parse_sample_keys("# OPTIONAL_KEY=default_value\n")
        self.assertEqual(active, set())
        self.assertEqual(commented, {"OPTIONAL_KEY"})

    def test_blank_lines(self):
        active, commented = _parse_sample_keys("\nFOO=bar\n\n")
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, set())

    def test_empty_key_in_sample(self):
        active, commented = _parse_sample_keys("=value\nFOO=bar\n")
        self.assertEqual(active, {"FOO"})

    def test_commented_lowercase_key_ignored(self):
        content = "# some description with equals=sign\n# VALID_KEY=val\n"
        active, commented = _parse_sample_keys(content)
        self.assertEqual(commented, {"VALID_KEY"})


class ParseSettingsEnvRefsTests(TestCase):
    def test_parses_environ_get(self):
        content = 'FOO = os.environ.get("MY_VAR", "default")\n'
        self.assertEqual(_parse_settings_env_refs(content), {"MY_VAR"})

    def test_parses_single_quotes(self):
        self.assertEqual(_parse_settings_env_refs("os.environ.get('MY_VAR')\n"), {"MY_VAR"})

    def test_multiple_refs(self):
        content = 'A = os.environ.get("VAR_A", "")\nB = os.environ.get("VAR_B", "0")\n'
        self.assertEqual(_parse_settings_env_refs(content), {"VAR_A", "VAR_B"})

    def test_no_refs(self):
        self.assertEqual(_parse_settings_env_refs("x = 1\n"), set())


# ---------------------------------------------------------------------------
# Environment check tests
# ---------------------------------------------------------------------------


_VersionInfo = namedtuple("_VersionInfo", ["major", "minor", "micro", "releaselevel", "serial"])


class CheckPythonVersionTests(TestCase):
    @patch(
        "apps.checkers.preflight.checks.sys.version_info",
        _VersionInfo(3, 9, 0, "final", 0),
    )
    def test_old_python(self):
        results = check_python_version()
        self.assertEqual(results[0].level, "error")
        self.assertIn("3.9", results[0].message)

    @patch("apps.checkers.preflight.checks.sys.base_prefix", "/usr")
    @patch("apps.checkers.preflight.checks.sys.prefix", "/usr")
    @patch(
        "apps.checkers.preflight.checks.sys.version_info",
        _VersionInfo(3, 12, 0, "final", 0),
    )
    def test_outside_venv(self):
        results = check_python_version()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("outside virtualenv", results[0].message)

    @patch("apps.checkers.preflight.checks.sys.base_prefix", "/usr")
    @patch("apps.checkers.preflight.checks.sys.prefix", "/some/other/venv")
    @patch(
        "apps.checkers.preflight.checks.sys.version_info",
        _VersionInfo(3, 12, 0, "final", 0),
    )
    def test_non_project_venv(self):
        results = check_python_version()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("not using project .venv", results[0].message)

    @patch("apps.checkers.preflight.checks.sys.base_prefix", "/usr")
    @patch("apps.checkers.preflight.checks.sys.prefix", "/project/.venv")
    @patch(
        "apps.checkers.preflight.checks.sys.version_info",
        _VersionInfo(3, 12, 0, "final", 0),
    )
    def test_project_venv_ok(self):
        results = check_python_version()
        self.assertEqual(results[0].level, "ok")
        self.assertIn(".venv", results[0].message)


class CheckUvInstalledTests(TestCase):
    @patch("apps.checkers.preflight.checks.shutil.which", return_value="/usr/bin/uv")
    def test_uv_found(self, _mock):
        results = check_uv_installed()
        self.assertEqual(results[0].level, "ok")

    @patch("apps.checkers.preflight.checks.shutil.which", return_value=None)
    def test_uv_not_found(self, _mock):
        results = check_uv_installed()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("not installed", results[0].message)


class CheckVenvExistsTests(TestCase):
    @patch("apps.checkers.preflight.checks._path_exists", return_value=True)
    def test_venv_found(self, _mock):
        results = check_venv_exists(Path("/fake"))
        self.assertEqual(results[0].level, "ok")

    @patch("apps.checkers.preflight.checks._path_exists", return_value=False)
    def test_venv_not_found(self, _mock):
        results = check_venv_exists(Path("/fake"))
        self.assertEqual(results[0].level, "warn")


class CheckEnvFileExistsTests(TestCase):
    @patch("apps.checkers.preflight.checks._path_exists", return_value=True)
    def test_env_found(self, _mock):
        results = check_env_file_exists(Path("/fake"))
        self.assertEqual(results[0].level, "ok")

    @patch("apps.checkers.preflight.checks._path_exists", return_value=False)
    def test_env_not_found(self, _mock):
        results = check_env_file_exists(Path("/fake"))
        self.assertEqual(results[0].level, "error")


class CheckProjectWritableTests(TestCase):
    @patch("apps.checkers.preflight.checks._is_writable", return_value=True)
    def test_writable(self, _mock):
        results = check_project_writable(Path("/fake"))
        self.assertEqual(results[0].level, "ok")

    @patch("apps.checkers.preflight.checks._is_writable", return_value=False)
    def test_not_writable(self, _mock):
        results = check_project_writable(Path("/fake"))
        self.assertEqual(results[0].level, "warn")


class CheckDiskSpaceTests(TestCase):
    DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])

    @patch("apps.checkers.preflight.checks.shutil.disk_usage")
    def test_enough_space(self, mock_usage):
        mock_usage.return_value = self.DiskUsage(
            total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3
        )
        results = check_disk_space(Path("/fake"))
        self.assertEqual(results[0].level, "ok")
        self.assertIn("50.0GB", results[0].message)

    @patch("apps.checkers.preflight.checks.shutil.disk_usage")
    def test_low_space(self, mock_usage):
        mock_usage.return_value = self.DiskUsage(
            total=100 * 1024**3, used=99.5 * 1024**3, free=int(0.5 * 1024**3)
        )
        results = check_disk_space(Path("/fake"))
        self.assertEqual(results[0].level, "warn")
        self.assertIn("Low disk space", results[0].message)

    @patch("apps.checkers.preflight.checks.shutil.disk_usage", side_effect=OSError)
    def test_os_error(self, _mock):
        results = check_disk_space(Path("/fake"))
        self.assertEqual(results[0].level, "warn")
        self.assertIn("Could not check", results[0].message)


# ---------------------------------------------------------------------------
# Database check tests
# ---------------------------------------------------------------------------


class CheckDatabaseConnectionTests(TestCase):
    def test_default_connection_ok(self):
        results = check_database_connection()
        ok_results = [r for r in results if r.level == "ok"]
        self.assertTrue(len(ok_results) >= 1)
        self.assertTrue(any("default" in r.message for r in ok_results))

    @patch("django.db.connections")
    def test_connection_failure(self, mock_connections):
        mock_conn = MagicMock()
        mock_conn.ensure_connection.side_effect = Exception("connection refused")
        mock_connections.__iter__ = MagicMock(return_value=iter(["default"]))
        mock_connections.__getitem__ = MagicMock(return_value=mock_conn)
        results = check_database_connection()
        self.assertEqual(results[0].level, "error")
        self.assertIn("Cannot connect", results[0].message)


class CheckPendingMigrationsTests(TestCase):
    def test_no_pending(self):
        results = check_pending_migrations()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("No pending", results[0].message)

    @patch("django.db.migrations.executor.MigrationExecutor")
    @patch("django.db.connections")
    def test_pending_migrations_found(self, mock_connections, mock_executor_cls):
        mock_conn = MagicMock()
        mock_connections.__getitem__ = MagicMock(return_value=mock_conn)
        mock_executor = MagicMock()
        mock_executor.migration_plan.return_value = [("fake_migration",)]
        mock_executor_cls.return_value = mock_executor
        results = check_pending_migrations()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("1 pending", results[0].message)

    @patch("django.db.connections")
    def test_migration_check_failure(self, mock_connections):
        mock_conn = MagicMock()
        mock_conn.ensure_connection.side_effect = Exception("db error")
        mock_connections.__getitem__ = MagicMock(return_value=mock_conn)
        results = check_pending_migrations()
        self.assertEqual(results[0].level, "error")
        self.assertIn("Migration check failed", results[0].message)


# ---------------------------------------------------------------------------
# Security check tests
# ---------------------------------------------------------------------------


class CheckDebugModeTests(TestCase):
    @override_settings(DEBUG=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_debug_on_in_prod(self):
        results = check_debug_mode()
        self.assertEqual(results[0].level, "error")
        self.assertIn("DEBUG", results[0].message)

    @override_settings(DEBUG=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_debug_on_in_dev_ok(self):
        results = check_debug_mode()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("on", results[0].message)

    @override_settings(DEBUG=False)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_debug_off_in_prod(self):
        results = check_debug_mode()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("off", results[0].message)


class CheckSecretKeyStrengthTests(TestCase):
    @override_settings(SECRET_KEY="a" * 50)
    def test_strong_key(self):
        results = check_secret_key_strength()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("50", results[0].message)

    @override_settings(SECRET_KEY="short")
    def test_weak_key(self):
        results = check_secret_key_strength()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("5 chars", results[0].message)

    @override_settings(SECRET_KEY="a" * 100)
    def test_very_strong_key(self):
        results = check_secret_key_strength()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("100", results[0].message)


class CheckAllowedHostsTests(TestCase):
    @override_settings(ALLOWED_HOSTS=[])
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_empty_in_prod(self):
        results = check_allowed_hosts()
        self.assertEqual(results[0].level, "error")

    @override_settings(ALLOWED_HOSTS=["*"])
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_wildcard(self):
        results = check_allowed_hosts()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("wildcard", results[0].message)

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_configured_ok(self):
        results = check_allowed_hosts()
        self.assertEqual(results[0].level, "ok")

    @override_settings(ALLOWED_HOSTS=[])
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_empty_in_dev_not_error(self):
        # In dev, empty ALLOWED_HOSTS is not flagged as error (no prod check),
        # but wildcard check also doesn't trigger, so it's "ok"
        results = check_allowed_hosts()
        self.assertEqual(results[0].level, "ok")


class CheckEnvFilePermissionsTests(TestCase):
    @patch("apps.checkers.preflight.checks._path_exists", return_value=False)
    def test_no_env_file(self, _mock):
        results = check_env_file_permissions(Path("/fake"))
        self.assertEqual(results, [])

    @patch("apps.checkers.preflight.checks._path_exists", return_value=True)
    def test_world_readable(self, _mock):
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_mode = 0o644
            results = check_env_file_permissions(Path("/fake"))
        self.assertEqual(results[0].level, "warn")
        self.assertIn("world-readable", results[0].message)

    @patch("apps.checkers.preflight.checks._path_exists", return_value=True)
    def test_good_permissions(self, _mock):
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_mode = 0o600
            results = check_env_file_permissions(Path("/fake"))
        self.assertEqual(results[0].level, "ok")

    @patch("apps.checkers.preflight.checks._path_exists", return_value=True)
    def test_stat_os_error(self, _mock):
        with patch.object(Path, "stat", side_effect=OSError):
            results = check_env_file_permissions(Path("/fake"))
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Config consistency check tests
# ---------------------------------------------------------------------------


class CheckEnvConsistencyTests(TestCase):
    @patch("apps.checkers.preflight.checks._read_file")
    def test_missing_env_returns_empty(self, mock_read):
        mock_read.return_value = None
        results = check_env_consistency(Path("/fake"))
        self.assertEqual(results, [])

    @patch("apps.checkers.preflight.checks._read_file")
    def test_missing_sample(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            return None

        mock_read.side_effect = side_effect
        results = check_env_consistency(Path("/fake"))
        self.assertTrue(any(".env.sample not found" in r.message for r in results))

    @patch("apps.checkers.preflight.checks._read_file")
    def test_sample_key_missing_from_env(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\nMISSING_KEY=default\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        results = check_env_consistency(Path("/fake"))
        self.assertTrue(any("MISSING_KEY" in r.message for r in results))

    @patch("apps.checkers.preflight.checks._read_file")
    def test_env_key_not_in_sample(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\nEXTRA=val\n"
            if path.name == ".env.sample":
                return "FOO=bar\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        results = check_env_consistency(Path("/fake"))
        self.assertTrue(any("EXTRA" in r.message for r in results))

    @patch("apps.checkers.preflight.checks._read_file")
    def test_settings_ref_missing_from_sample(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("UNDOCUMENTED_VAR", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = check_env_consistency(Path("/fake"))
        self.assertTrue(any("UNDOCUMENTED_VAR" in r.message for r in results))

    @patch("apps.checkers.preflight.checks._read_file")
    def test_sample_key_unreferenced_in_settings(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\nSTALE=val\n"
            if path.name == ".env.sample":
                return "FOO=bar\nSTALE=old\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("FOO", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = check_env_consistency(Path("/fake"))
        self.assertTrue(any("STALE" in r.message for r in results))

    @patch("apps.checkers.preflight.checks._read_file")
    def test_all_consistent(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=default\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("FOO", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = check_env_consistency(Path("/fake"))
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)

    @patch("apps.checkers.preflight.checks._read_file")
    def test_no_settings_file(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\n"
            return None

        mock_read.side_effect = side_effect
        results = check_env_consistency(Path("/fake"))
        self.assertTrue(any(r.level == "ok" for r in results))


class CheckClusterCoherenceTests(TestCase):
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="node-1",
    )
    def test_agent_and_hub_conflict(self):
        results = check_cluster_coherence()
        self.assertEqual(results[0].level, "error")
        self.assertIn("conflict", results[0].message.lower())

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="node-1",
    )
    def test_agent_without_secret(self):
        results = check_cluster_coherence()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("WEBHOOK_SECRET_CLUSTER" in r.message for r in warns))

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="",
    )
    def test_agent_without_instance_id(self):
        results = check_cluster_coherence()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("INSTANCE_ID" in r.message for r in warns))

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="",
    )
    def test_hub_without_secret(self):
        results = check_cluster_coherence()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("WEBHOOK_SECRET_CLUSTER" in r.message for r in errors))

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="",
    )
    def test_standalone_ok(self):
        results = check_cluster_coherence()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("standalone", results[0].message)

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="node-1",
    )
    def test_valid_agent_ok(self):
        results = check_cluster_coherence()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("agent", results[0].message)

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="",
    )
    def test_valid_hub_ok(self):
        results = check_cluster_coherence()
        self.assertEqual(results[0].level, "ok")
        self.assertIn("hub", results[0].message)


class CheckCeleryEagerTests(TestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_eager_in_prod(self):
        results = check_celery_eager()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "warn")
        self.assertIn("eager", results[0].message.lower())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_eager_in_dev_ok(self):
        results = check_celery_eager()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_not_eager_in_prod_ok(self):
        results = check_celery_eager()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")


class CheckMetricsConfigTests(TestCase):
    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="custom-host",
    )
    def test_statsd_host_set_but_backend_logging(self):
        results = check_metrics_config()
        self.assertEqual(results[0].level, "info")
        self.assertIn("StatsD", results[0].message)

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="statsd",
        STATSD_HOST="localhost",
    )
    def test_statsd_backend_with_localhost(self):
        results = check_metrics_config()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("STATSD_HOST", results[0].message)

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="localhost",
    )
    def test_default_config_ok(self):
        results = check_metrics_config()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="statsd",
        STATSD_HOST="statsd.example.com",
    )
    def test_statsd_properly_configured(self):
        results = check_metrics_config()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")


# ---------------------------------------------------------------------------
# Django system check tests
# ---------------------------------------------------------------------------


class CheckDjangoSystemTests(TestCase):
    @patch("apps.checkers.preflight.checks.run_checks", return_value=[])
    def test_no_issues_returns_ok(self, mock_run):
        results = check_django_system()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")
        self.assertIn("passed", results[0].message.lower())

    @patch("apps.checkers.preflight.checks.run_checks")
    def test_warning_mapped_to_warn(self, mock_run):
        from django.core.checks import WARNING as DJANGO_WARNING
        from django.core.checks import CheckMessage

        msg = CheckMessage(DJANGO_WARNING, "some warning", hint="fix it", id="test.W001")
        mock_run.return_value = [msg]
        results = check_django_system()
        self.assertEqual(results[0].level, "warn")
        self.assertIn("test.W001", results[0].message)

    @patch("apps.checkers.preflight.checks.run_checks")
    def test_error_mapped_to_error(self, mock_run):
        from django.core.checks import ERROR as DJANGO_ERROR
        from django.core.checks import CheckMessage

        msg = CheckMessage(DJANGO_ERROR, "some error", hint="fix this", id="test.E001")
        mock_run.return_value = [msg]
        results = check_django_system()
        self.assertEqual(results[0].level, "error")
        self.assertIn("test.E001", results[0].message)

    @patch("apps.checkers.preflight.checks.run_checks")
    def test_info_mapped_to_info(self, mock_run):
        from django.core.checks import INFO as DJANGO_INFO
        from django.core.checks import CheckMessage

        msg = CheckMessage(DJANGO_INFO, "some info", id="test.I001")
        mock_run.return_value = [msg]
        results = check_django_system()
        self.assertEqual(results[0].level, "info")

    @patch("apps.checkers.preflight.checks.run_checks")
    def test_debug_mapped_to_info(self, mock_run):
        from django.core.checks import DEBUG as DJANGO_DEBUG
        from django.core.checks import CheckMessage

        msg = CheckMessage(DJANGO_DEBUG, "debug msg")
        mock_run.return_value = [msg]
        results = check_django_system()
        self.assertEqual(results[0].level, "info")

    @patch("apps.checkers.preflight.checks.run_checks")
    def test_message_without_id_omits_id_prefix(self, mock_run):
        from django.core.checks import WARNING as DJANGO_WARNING
        from django.core.checks import CheckMessage

        msg = CheckMessage(DJANGO_WARNING, "plain warning")
        mock_run.return_value = [msg]
        results = check_django_system()
        self.assertNotIn("[", results[0].message)
        self.assertIn("plain warning", results[0].message)

    @patch("apps.checkers.preflight.checks.run_checks")
    def test_critical_mapped_to_error(self, mock_run):
        from django.core.checks import CRITICAL as DJANGO_CRITICAL
        from django.core.checks import CheckMessage

        msg = CheckMessage(DJANGO_CRITICAL, "critical error", id="test.C001")
        mock_run.return_value = [msg]
        results = check_django_system()
        self.assertEqual(results[0].level, "error")


# ---------------------------------------------------------------------------
# Pipeline state check tests
# ---------------------------------------------------------------------------


class CheckPipelineStateTests(TestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_active_pipeline_with_eager_celery(self):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(name="test", config={}, is_active=True)
        results = check_pipeline_state()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("eager" in r.message.lower() for r in warns))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_no_active_channels(self):
        results = check_pipeline_state()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("notification channel" in r.message.lower() for r in warns))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_no_active_definitions(self):
        results = check_pipeline_state()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("pipeline definition" in r.message.lower() for r in infos))

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=False,
    )
    def test_intelligence_fallback_disabled(self):
        from apps.intelligence.models import IntelligenceProvider

        IntelligenceProvider.objects.create(name="test-ai", provider="claude", is_active=True)
        results = check_pipeline_state()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("fallback" in r.message.lower() for r in infos))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_all_ok(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(name="test", config={}, is_active=True)
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        results = check_pipeline_state()
        ok_results = [r for r in results if r.level == "ok"]
        self.assertTrue(len(ok_results) >= 1)
        self.assertTrue(any("pipeline" in r.message.lower() for r in ok_results))


# ---------------------------------------------------------------------------
# Installation state check tests
# ---------------------------------------------------------------------------


class CheckInstallationStateTests(TestCase):
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.preflight.checks._path_exists")
    def test_aliases_missing_in_dev(self, mock_exists):
        mock_exists.side_effect = lambda p: "aliases" not in str(p)
        results = check_installation_state(Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("aliases" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.preflight.checks._path_exists")
    def test_precommit_missing_in_dev(self, mock_exists):
        mock_exists.side_effect = lambda p: "pre-commit" not in str(p)
        results = check_installation_state(Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("pre-commit" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    @patch("apps.checkers.preflight.checks._path_exists")
    @patch("apps.checkers.preflight.checks._check_crontab")
    @patch("apps.checkers.preflight.checks._is_writable")
    def test_cron_missing_in_prod(self, mock_writable, mock_cron, mock_exists):
        mock_exists.return_value = True
        mock_cron.return_value = False
        mock_writable.return_value = True
        results = check_installation_state(Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("cron" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.preflight.checks._path_exists")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    @patch("apps.checkers.preflight.checks._is_writable")
    def test_logs_dir_not_writable(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = False
        results = check_installation_state(Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("logs" in r.message.lower() for r in errors))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.preflight.checks._path_exists")
    @override_settings(
        LOGS_DIR=Path("/fake/logs"),
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": Path("/fake/db.sqlite3"),
            }
        },
    )
    @patch("apps.checkers.preflight.checks._is_writable")
    def test_db_file_not_writable(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.side_effect = lambda p: "db" not in str(p)
        results = check_installation_state(Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("database" in r.message.lower() for r in errors))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.preflight.checks._path_exists")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    @patch("apps.checkers.preflight.checks._is_writable")
    def test_all_ok_in_dev(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = True
        results = check_installation_state(Path("/fake"))
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)

    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    @patch("apps.checkers.preflight.checks._path_exists")
    @patch("apps.checkers.preflight.checks._check_crontab")
    @patch("apps.checkers.preflight.checks._is_writable")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    def test_all_ok_in_prod(self, mock_writable, mock_cron, mock_exists):
        mock_exists.return_value = True
        mock_cron.return_value = True
        mock_writable.return_value = True
        results = check_installation_state(Path("/fake"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.preflight.checks._path_exists")
    @override_settings(
        LOGS_DIR=Path("/fake/logs"),
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "mydb",
            }
        },
    )
    @patch("apps.checkers.preflight.checks._is_writable")
    def test_non_sqlite_db_skips_file_check(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = True
        results = check_installation_state(Path("/fake"))
        self.assertFalse(any("database" in r.message.lower() for r in results))


# ---------------------------------------------------------------------------
# Deployment check tests
# ---------------------------------------------------------------------------


class CheckDeploymentTests(TestCase):
    @patch.dict(os.environ, {"DEPLOY_METHOD": "bare"})
    @patch("apps.checkers.preflight.checks._systemd_unit_exists", return_value=False)
    def test_bare_no_systemd_returns_ok(self, _mock):
        results = check_deployment(Path("/fake"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "ok")
        self.assertIn("dev/bare", results[0].message)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "docker"})
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_docker_daemon_not_running(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        results = check_deployment(Path("/fake"))
        self.assertEqual(results[0].level, "error")
        self.assertIn("Docker daemon", results[0].message)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "docker"})
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_docker_daemon_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["docker", "info"], 10)
        results = check_deployment(Path("/fake"))
        self.assertEqual(results[0].level, "error")
        self.assertIn("Docker daemon", results[0].message)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "docker"})
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_docker_services_running(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = '{"State": "Running"}'
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        results = check_deployment(Path("/fake"))
        ok_results = [r for r in results if r.level == "ok"]
        # Docker daemon + 3 services
        self.assertEqual(len(ok_results), 4)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "docker"})
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_docker_service_not_running(self, mock_run):
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # docker info
                result.stdout = ""
                result.returncode = 0
            else:
                # docker compose ps
                result.stdout = '{"State": "Exited"}'
                result.returncode = 0
            return result

        mock_run.side_effect = side_effect
        results = check_deployment(Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(len(errors) >= 1)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "docker"})
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_docker_compose_check_fails(self, mock_run):
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                result = MagicMock()
                result.stdout = ""
                result.returncode = 0
                return result
            raise OSError("cannot check")

        mock_run.side_effect = side_effect
        results = check_deployment(Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(len(errors) >= 1)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "bare"})
    @patch("apps.checkers.preflight.checks._systemd_unit_exists", return_value=True)
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_systemd_services_active(self, mock_run, _mock_systemd):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        with patch.object(Path, "exists", return_value=True):
            results = check_deployment(Path("/fake"))
        ok_results = [r for r in results if r.level == "ok"]
        # 2 services + redis + gunicorn socket
        self.assertTrue(len(ok_results) >= 3)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "bare"})
    @patch("apps.checkers.preflight.checks._systemd_unit_exists", return_value=True)
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_systemd_services_inactive(self, mock_run, _mock_systemd):
        mock_result = MagicMock()
        mock_result.returncode = 3  # inactive
        mock_run.return_value = mock_result

        with patch.object(Path, "exists", return_value=False):
            results = check_deployment(Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(len(errors) >= 1)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "bare"})
    @patch("apps.checkers.preflight.checks._systemd_unit_exists", return_value=True)
    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=FileNotFoundError,
    )
    def test_systemd_command_not_found(self, _mock_run, _mock_systemd):
        with patch.object(Path, "exists", return_value=False):
            results = check_deployment(Path("/fake"))
        # Should get warn for services that can't be checked, error for redis
        self.assertTrue(len(results) >= 1)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "bare"})
    @patch("apps.checkers.preflight.checks._systemd_unit_exists", return_value=True)
    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["systemctl"], 5),
    )
    def test_systemd_timeout(self, _mock_run, _mock_systemd):
        with patch.object(Path, "exists", return_value=False):
            results = check_deployment(Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(len(warns) >= 1)

    @patch.dict(os.environ, {"DEPLOY_METHOD": "bare"})
    @patch("apps.checkers.preflight.checks._systemd_unit_exists", return_value=True)
    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=OSError("os error"),
    )
    def test_systemd_os_error(self, _mock_run, _mock_systemd):
        with patch.object(Path, "exists", return_value=False):
            results = check_deployment(Path("/fake"))
        self.assertTrue(len(results) >= 1)


class SystemdUnitExistsTests(TestCase):
    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_unit_found(self, mock_run):
        mock_run.return_value.stdout = "server-monitoring.service enabled"
        from apps.checkers.preflight.checks import _systemd_unit_exists

        self.assertTrue(_systemd_unit_exists())

    @patch("apps.checkers.preflight.checks.subprocess.run")
    def test_unit_not_found(self, mock_run):
        mock_run.return_value.stdout = ""
        from apps.checkers.preflight.checks import _systemd_unit_exists

        self.assertFalse(_systemd_unit_exists())

    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=FileNotFoundError,
    )
    def test_systemctl_not_found(self, _mock):
        from apps.checkers.preflight.checks import _systemd_unit_exists

        self.assertFalse(_systemd_unit_exists())

    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["systemctl"], 5),
    )
    def test_timeout(self, _mock):
        from apps.checkers.preflight.checks import _systemd_unit_exists

        self.assertFalse(_systemd_unit_exists())

    @patch(
        "apps.checkers.preflight.checks.subprocess.run",
        side_effect=OSError("os error"),
    )
    def test_os_error(self, _mock):
        from apps.checkers.preflight.checks import _systemd_unit_exists

        self.assertFalse(_systemd_unit_exists())


# ---------------------------------------------------------------------------
# Integration: run_all
# ---------------------------------------------------------------------------


class RunAllTests(TestCase):
    @patch("apps.checkers.preflight.checks.check_deployment", return_value=[])
    @patch(
        "apps.checkers.preflight.checks.check_installation_state",
        return_value=[CheckResult(level="ok", message="install ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_pipeline_state",
        return_value=[CheckResult(level="ok", message="pipeline ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_django_system",
        return_value=[CheckResult(level="ok", message="django ok")],
    )
    @patch("apps.checkers.preflight.checks.check_metrics_config", return_value=[])
    @patch("apps.checkers.preflight.checks.check_celery_eager", return_value=[])
    @patch(
        "apps.checkers.preflight.checks.check_cluster_coherence",
        return_value=[CheckResult(level="ok", message="cluster ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_env_consistency",
        return_value=[CheckResult(level="ok", message="env ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_env_file_permissions",
        return_value=[CheckResult(level="ok", message="perms ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_allowed_hosts",
        return_value=[CheckResult(level="ok", message="hosts ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_secret_key_strength",
        return_value=[CheckResult(level="ok", message="key ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_debug_mode",
        return_value=[CheckResult(level="ok", message="debug ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_pending_migrations",
        return_value=[CheckResult(level="ok", message="migrations ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_database_connection",
        return_value=[CheckResult(level="ok", message="db ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_disk_space",
        return_value=[CheckResult(level="ok", message="disk ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_project_writable",
        return_value=[CheckResult(level="ok", message="writable ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_env_file_exists",
        return_value=[CheckResult(level="ok", message="env file ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_venv_exists",
        return_value=[CheckResult(level="ok", message="venv ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_uv_installed",
        return_value=[CheckResult(level="ok", message="uv ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_python_version",
        return_value=[CheckResult(level="ok", message="python ok")],
    )
    def test_returns_flat_list(self, *_mocks):
        results = run_all(Path("/fake"))
        self.assertIsInstance(results, list)
        self.assertTrue(all(isinstance(r, CheckResult) for r in results))
        self.assertTrue(len(results) >= 15)

    @patch("apps.checkers.preflight.checks.check_deployment", return_value=[])
    @patch(
        "apps.checkers.preflight.checks.check_installation_state",
        return_value=[CheckResult(level="ok", message="install ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_pipeline_state",
        return_value=[CheckResult(level="ok", message="pipeline ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_django_system",
        return_value=[CheckResult(level="ok", message="django ok")],
    )
    @patch("apps.checkers.preflight.checks.check_metrics_config", return_value=[])
    @patch("apps.checkers.preflight.checks.check_celery_eager", return_value=[])
    @patch(
        "apps.checkers.preflight.checks.check_cluster_coherence",
        return_value=[CheckResult(level="ok", message="cluster ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_env_consistency",
        return_value=[CheckResult(level="ok", message="env ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_env_file_permissions",
        return_value=[CheckResult(level="ok", message="perms ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_allowed_hosts",
        return_value=[CheckResult(level="ok", message="hosts ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_secret_key_strength",
        return_value=[CheckResult(level="ok", message="key ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_debug_mode",
        return_value=[CheckResult(level="ok", message="debug ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_pending_migrations",
        return_value=[CheckResult(level="ok", message="migrations ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_database_connection",
        return_value=[CheckResult(level="ok", message="db ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_disk_space",
        return_value=[CheckResult(level="ok", message="disk ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_project_writable",
        return_value=[CheckResult(level="ok", message="writable ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_env_file_exists",
        return_value=[CheckResult(level="error", message=".env file not found")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_venv_exists",
        return_value=[CheckResult(level="ok", message="venv ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_uv_installed",
        return_value=[CheckResult(level="ok", message="uv ok")],
    )
    @patch(
        "apps.checkers.preflight.checks.check_python_version",
        return_value=[CheckResult(level="ok", message="python ok")],
    )
    def test_includes_errors_from_individual_checks(self, *_mocks):
        results = run_all(Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(len(errors) >= 1)
        self.assertTrue(any(".env file not found" in r.message for r in errors))


class IsProductionTests(TestCase):
    """Tests for _is_production helper."""

    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_prod(self):
        from apps.checkers.preflight.checks import _is_production

        self.assertTrue(_is_production())

    @patch.dict(os.environ, {"DJANGO_ENV": "production"})
    def test_production(self):
        from apps.checkers.preflight.checks import _is_production

        self.assertTrue(_is_production())

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_dev(self):
        from apps.checkers.preflight.checks import _is_production

        self.assertFalse(_is_production())

    @patch.dict(os.environ, {}, clear=True)
    def test_default(self):
        from apps.checkers.preflight.checks import _is_production

        self.assertFalse(_is_production())
