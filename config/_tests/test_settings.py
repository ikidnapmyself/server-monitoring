"""Tests for config/settings.py — specifically the SECRET_KEY guard."""

import importlib
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
