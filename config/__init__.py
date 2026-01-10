# Celery application bootstrap (so `celery -A config worker` works)
from .celery import app as celery_app

__all__ = ["celery_app"]
