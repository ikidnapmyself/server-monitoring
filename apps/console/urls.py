from django.urls import path

from apps.console.views import OverviewView

app_name = "console"

urlpatterns = [
    path("", OverviewView.as_view(), name="overview"),
]
