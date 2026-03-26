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
