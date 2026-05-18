"""Tests for the LOGGING configuration."""

from django.conf import settings


def test_logging_has_events_and_heartbeat_handlers():
    handlers = settings.LOGGING["handlers"]
    assert "events_file" in handlers
    assert "heartbeat_file" in handlers
    assert "console" in handlers


def test_logging_does_not_have_legacy_django_log_handler():
    # Single-cut migration — django.log handler is removed.
    handlers = settings.LOGGING["handlers"]
    assert "file" not in handlers  # the old handler name


def test_events_file_handler_uses_json_formatter():
    handlers = settings.LOGGING["handlers"]
    assert handlers["events_file"]["formatter"] == "json"
    assert handlers["events_file"]["class"] == "logging.handlers.RotatingFileHandler"


def test_heartbeat_file_handler_uses_json_formatter():
    handlers = settings.LOGGING["handlers"]
    assert handlers["heartbeat_file"]["formatter"] == "json"
    assert handlers["heartbeat_file"]["class"] == "logging.handlers.RotatingFileHandler"


def test_heartbeat_logger_routes_only_to_heartbeat_file():
    loggers = settings.LOGGING["loggers"]
    hb = loggers["apps.observability.heartbeat"]
    assert hb["handlers"] == ["heartbeat_file"]
    assert hb["propagate"] is False


def test_observability_size_settings_have_sane_defaults():
    assert settings.OBSERVABILITY_EVENTS_MAX_BYTES >= 1024 * 1024
    assert settings.OBSERVABILITY_EVENTS_BACKUPS >= 1
    assert settings.OBSERVABILITY_HEARTBEATS_MAX_BYTES >= 1024
    assert settings.OBSERVABILITY_HEARTBEATS_BACKUPS >= 1
