"""
URL configuration for the alerts app.
"""

from django.urls import path

from apps.alerts.views import AlertWebhookView


app_name = "alerts"

urlpatterns = [
    # Generic webhook (auto-detect driver)
    path("webhook/", AlertWebhookView.as_view(), name="webhook"),

    # Driver-specific webhooks
    path("webhook/<str:driver>/", AlertWebhookView.as_view(), name="webhook_driver"),
]

