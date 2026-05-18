"""App registration smoke tests."""

from django.apps import apps


def test_observability_app_is_registered():
    assert apps.is_installed("apps.observability")


def test_observability_config_label():
    config = apps.get_app_config("observability")
    assert config.name == "apps.observability"
    assert config.label == "observability"
