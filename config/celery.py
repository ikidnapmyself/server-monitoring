"""Celery application bootstrap for this Django project.

This enables background orchestration of the pipeline:
alerts → checkers → intelligence → notify.

Run workers with something like:
- celery -A config worker -l info

Broker/result backend are configured via Django settings (see config/settings.py).
"""

from __future__ import annotations

import os

from celery import Celery

from config.env import load_env

load_env()

# Ensure Django settings are loaded when Celery starts.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("server-maintanence")

# Load Celery config from Django settings using CELERY_* namespace.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Autodiscover tasks.py in Django apps.
app.autodiscover_tasks()
