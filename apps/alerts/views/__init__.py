"""
Alerts app views.

This package contains HTTP endpoints for alert ingestion.
Views are organized by endpoint/functionality.
"""

from apps.alerts.views.webhook import AlertWebhookView

__all__ = ["AlertWebhookView"]
