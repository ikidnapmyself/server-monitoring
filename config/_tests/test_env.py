"""Tests for config/env.py — dotenv loading and dev-env detection."""

from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from config.env import _should_load_dev_env, load_env


class TestShouldLoadDevEnv(SimpleTestCase):
    def test_returns_true_for_dev(self):
        with patch.dict("os.environ", {"DJANGO_ENV": "dev"}):
            assert _should_load_dev_env() is True

    def test_returns_true_for_development(self):
        with patch.dict("os.environ", {"DJANGO_ENV": "development"}):
            assert _should_load_dev_env() is True

    def test_returns_true_for_local(self):
        with patch.dict("os.environ", {"DJANGO_ENV": "local"}):
            assert _should_load_dev_env() is True

    def test_returns_false_for_production(self):
        with patch.dict("os.environ", {"DJANGO_ENV": "production"}):
            assert _should_load_dev_env() is False

    def test_returns_false_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _should_load_dev_env() is False


class TestLoadEnv(SimpleTestCase):
    @patch("config.env.load_dotenv")
    def test_loads_env_file(self, mock_load_dotenv):
        base = Path("/fake/project")
        with patch.dict("os.environ", {"DJANGO_ENV": "production"}):
            load_env(base)
        mock_load_dotenv.assert_called_once_with(base / ".env", override=False)

    @patch("config.env.load_dotenv")
    def test_loads_dev_env_when_django_env_dev(self, mock_load_dotenv):
        base = Path("/fake/project")
        with patch.dict("os.environ", {"DJANGO_ENV": "dev"}):
            load_env(base)
        assert mock_load_dotenv.call_count == 2
        mock_load_dotenv.assert_any_call(base / ".env", override=False)
        mock_load_dotenv.assert_any_call(base / ".env.dev", override=False)

    @patch("config.env.load_dotenv")
    def test_defaults_base_dir_when_none(self, mock_load_dotenv):
        with patch.dict("os.environ", {"DJANGO_ENV": "production"}):
            load_env(None)
        call_args = mock_load_dotenv.call_args[0][0]
        assert call_args.name == ".env"
        assert call_args.parent.name == "server-maintanence"
