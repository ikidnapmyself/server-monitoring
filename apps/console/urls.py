from django.urls import path

from apps.console.views import (
    IncidentActionView,
    IncidentDetailView,
    IncidentListView,
    IncidentRerunView,
    OverviewView,
)

app_name = "console"

urlpatterns = [
    path("", OverviewView.as_view(), name="overview"),
    path("alerts/", OverviewView.as_view(), name="alerts"),
    path("alerts/<int:pk>/", OverviewView.as_view(), name="alert"),
    path("incidents/", IncidentListView.as_view(), name="incidents"),
    path("incidents/<int:pk>/", IncidentDetailView.as_view(), name="incident"),
    path("incidents/<int:pk>/rerun/", IncidentRerunView.as_view(), name="incident-rerun"),
    path("incidents/<int:pk>/<str:action>/", IncidentActionView.as_view(), name="incident-action"),
]
