"""Shared fixtures for apps.observability tests.

The heartbeat logger's RotatingFileHandler is wired at startup with a
filename captured from ``settings.LOGS_DIR`` at that moment. Tests that
emit heartbeats (directly via ``emit_heartbeat()`` / ``heartbeat()`` or
indirectly through management commands) need that handler swapped to a
per-test ``tmp_path`` location, otherwise heartbeats land in the real
``LOGS_DIR`` and tests can't assert on the file contents.

This fixture is ``autouse=True`` so every test in this package gets the
redirected handler. Tests that don't emit heartbeats (e.g. those in
``test_heartbeat_reader.py`` and ``test_checks.py`` which write the
JSONL file directly) are unaffected — the fixture just rebinds an
unused handler.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

from apps.observability.formatter import JsonLineFormatter


@pytest.fixture(autouse=True)
def redirect_heartbeat_handler(tmp_path, settings):
    """Swap the heartbeat logger's handlers to write into tmp_path."""
    settings.LOGS_DIR = tmp_path
    handler = RotatingFileHandler(
        tmp_path / "heartbeats.jsonl",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(JsonLineFormatter())
    logger = logging.getLogger("apps.observability.heartbeat")
    old_handlers = logger.handlers[:]
    old_propagate = logger.propagate
    logger.handlers = [handler]
    logger.propagate = False
    try:
        yield
    finally:
        logger.handlers = old_handlers
        logger.propagate = old_propagate
        handler.close()
