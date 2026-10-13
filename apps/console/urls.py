from django.urls import path

from apps.console.views import IncidentListView, OverviewView

app_name = "console"

urlpatterns = [
    path("", OverviewView.as_view(), name="overview"),
    path("incidents/", IncidentListView.as_view(), name="incidents"),
]
