"""The console is the only UI. Django's admin is uninstalled and stays that way."""

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings

from config.middleware import rate_limit
from config.middleware.constants import EXEMPT_PATH_PREFIXES

ROOT = Path(settings.BASE_DIR)
ADMIN_IMPORT = re.compile(
    r"^\s*(?:from django\.contrib import admin\b"
    r"|from django\.contrib\.admin\b"
    r"|import django\.contrib\.admin\b)",
    re.M,
)


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/admin/", "/admin/login/"])
def test_the_admin_urls_are_gone(client, path):
    assert client.get(path).status_code == 404


def test_the_admin_app_is_not_installed():
    assert "django.contrib.admin" not in settings.INSTALLED_APPS
    assert "admin" not in {config.label for config in apps.get_app_configs()}


def _sources():
    for top in ("apps", "config"):
        for path in (ROOT / top).rglob("*.py"):
            if "migrations" not in path.parts:
                yield path


def test_no_module_imports_the_admin():
    importers = [
        str(path.relative_to(ROOT)) for path in _sources() if ADMIN_IMPORT.search(path.read_text())
    ]
    assert importers == []


def test_the_import_check_catches_an_admin_import():
    assert ADMIN_IMPORT.search("from django.contrib import admin\n")
    assert ADMIN_IMPORT.search("from django.contrib.admin.apps import AdminConfig\n")
    assert not ADMIN_IMPORT.search("from django.contrib import auth\n")


def test_no_middleware_or_rate_limit_names_the_admin():
    assert not any(prefix.startswith("/admin/") for prefix in EXEMPT_PATH_PREFIXES)
    for table in (settings.RATE_LIMITS, rate_limit.DEFAULT_RATE_LIMITS):
        assert not any(prefix.startswith("/admin/") for prefix in table)
