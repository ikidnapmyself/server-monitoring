"""Celery application bootstrap for this Django project.

This enables background orchestration of the pipeline:
alerts → checkers → intelligence → notify.

Run workers with something like:
- celery -A config worker -l info

Broker/result backend are configured via Django settings (see config/settings.py).
"""

from __future__ import annotations

import os
import uuid

from celery import Celery
from celery.signals import task_postrun, task_prerun

from apps.observability import context
from config.env import load_env

load_env()

# Ensure Django settings are loaded when Celery starts.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("server-maintanence")

# Load Celery config from Django settings using CELERY_* namespace.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Autodiscover tasks.py in Django apps.
app.autodiscover_tasks()


# --- Observability: bind trace_id/source ContextVars per task -----------------

_BIND_TOKENS: dict[str, object] = {}


@task_prerun.connect
def _obs_task_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **_):
    if task_id is None:
        return
    headers = getattr(getattr(task, "request", None), "headers", None) or {}
    trace_id = headers.get("trace_id") or str(uuid.uuid4())
    token = context.bind(trace_id=trace_id, source="celery")
    _BIND_TOKENS[task_id] = token


@task_postrun.connect
def _obs_task_postrun(sender=None, task_id=None, **_):
    token = _BIND_TOKENS.pop(task_id, None)
    if token is not None:
        context.restore(token)
