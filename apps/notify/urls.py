"""
URL configuration for the notify app.
"""

from django.urls import path

from apps.notify.views import DriversView, NotifyBatchView, NotifyView

app_name = "notify"

urlpatterns = [
    # Send notification (auto-detect or specify driver)
    path("send/", NotifyView.as_view(), name="send"),
    path("send/<str:driver>/", NotifyView.as_view(), name="send_driver"),
    # Batch notifications
    path("batch/", NotifyBatchView.as_view(), name="batch"),
    # Driver info
    path("drivers/", DriversView.as_view(), name="drivers"),
    path("drivers/<str:driver>/", DriversView.as_view(), name="driver_detail"),
]
