"""Tests for config/settings.py — specifically the SECRET_KEY guard and DATABASE_PATH normalization."""

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase


class TestSecretKeyRequired(SimpleTestCase):
    def test_missing_secret_key_raises_runtime_error(self):
        """Importing settings without DJANGO_SECRET_KEY must raise RuntimeError."""
        env = {"DJANGO_DEBUG": "0"}  # no DJANGO_SECRET_KEY
        with patch("config.env.load_dotenv"), patch.dict("os.environ", env, clear=True):
            with pytest.raises(RuntimeError, match="DJANGO_SECRET_KEY"):
                importlib.reload(__import__("config.settings", fromlist=["settings"]))


class TestDatabasePathNormalization(SimpleTestCase):
    """DATABASE_PATH env var should be anchored to BASE_DIR when relative."""

    _base_env = {
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_ALLOWED_HOSTS": "localhost",
    }

    def _reload_settings(self, extra_env: dict) -> object:
        env = {**self._base_env, **extra_env}
        with patch("config.env.load_dotenv"), patch.dict("os.environ", env, clear=True):
            return importlib.reload(__import__("config.settings", fromlist=["settings"]))

    def test_no_database_path_uses_base_dir(self):
        """When DATABASE_PATH is unset, the DB lives at BASE_DIR/db.sqlite3."""
        settings = self._reload_settings({})
        base_dir = Path(__file__).resolve().parent.parent.parent
        assert settings.DATABASES["default"]["NAME"] == base_dir / "db.sqlite3"

    def test_absolute_database_path_used_as_is(self):
        """An absolute DATABASE_PATH is used without modification."""
        settings = self._reload_settings({"DATABASE_PATH": "/var/data/mydb.sqlite3"})
        assert settings.DATABASES["default"]["NAME"] == Path("/var/data/mydb.sqlite3")

    def test_relative_database_path_anchored_to_base_dir(self):
        """A relative DATABASE_PATH is resolved relative to BASE_DIR, not CWD."""
        settings = self._reload_settings({"DATABASE_PATH": "data/mydb.sqlite3"})
        base_dir = Path(__file__).resolve().parent.parent.parent
        assert settings.DATABASES["default"]["NAME"] == base_dir / "data" / "mydb.sqlite3"


def test_staticfiles_dirs_includes_project_static():
    # Imported here, not at module scope: the tests above bind `settings` as a local
    # to a reloaded module, and a module-level import would read as the same object.
    from django.conf import settings

    assert settings.BASE_DIR / "static" in settings.STATICFILES_DIRS


MANIFEST_BACKEND = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
PLAIN_BACKEND = "django.contrib.staticfiles.storage.StaticFilesStorage"


class TestStaticStorageBackend(SimpleTestCase):
    """nginx serves /static/ as immutable for 30 days, so production names must be hashed."""

    _base_env = {"DJANGO_SECRET_KEY": "test-secret", "DJANGO_ALLOWED_HOSTS": "localhost"}

    def _backend(self, debug: str) -> str:
        env = {**self._base_env, "DJANGO_DEBUG": debug}
        with patch("config.env.load_dotenv"), patch.dict("os.environ", env, clear=True):
            module = importlib.reload(__import__("config.settings", fromlist=["settings"]))
        return module.STORAGES["staticfiles"]["BACKEND"]

    def test_debug_off_hashes_static_names(self):
        assert self._backend("0") == MANIFEST_BACKEND

    def test_debug_on_serves_plain_names(self):
        assert self._backend("1") == PLAIN_BACKEND

    def test_debug_flag_ignores_case(self):
        for value in ("TRUE", "Yes", "ON"):
            with self.subTest(value=value):
                assert self._backend(value) == PLAIN_BACKEND


def test_suite_runs_on_plain_static_storage():
    from django.conf import settings

    assert settings.STORAGES["staticfiles"]["BACKEND"] == PLAIN_BACKEND


def test_collectstatic_hashes_every_console_asset(tmp_path, settings):
    from django.contrib.staticfiles.storage import staticfiles_storage
    from django.core.management import call_command

    settings.STATIC_ROOT = tmp_path
    settings.STORAGES = {**settings.STORAGES, "staticfiles": {"BACKEND": MANIFEST_BACKEND}}

    call_command("collectstatic", "--noinput", verbosity=0)

    for name in (
        "console/console.css",
        "console/console.js",
        "console/vendor/htmx.min.js",
        "admin/css/ops.css",
    ):
        stored = staticfiles_storage.stored_name(name)
        assert stored != name
        assert (tmp_path / stored).is_file()
        assert staticfiles_storage.url(name) == f"/static/{stored}"


def test_the_console_owns_sign_in():
    from django.conf import settings
    from django.shortcuts import resolve_url

    assert resolve_url(settings.LOGIN_URL) == "/console/login/"
    assert resolve_url(settings.LOGIN_REDIRECT_URL) == "/console/"
