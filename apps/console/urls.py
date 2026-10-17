from django.urls import path

from apps.console.views import (
    AlertDetailView,
    AlertListView,
    IncidentActionView,
    IncidentDetailView,
    IncidentListView,
    IncidentRerunView,
    OverviewView,
)

app_name = "console"

urlpatterns = [
    path("", OverviewView.as_view(), name="overview"),
    path("alerts/", AlertListView.as_view(), name="alerts"),
    path("alerts/<int:pk>/", AlertDetailView.as_view(), name="alert"),
    path("alerts/<int:pk>/reevaluate/", AlertDetailView.as_view(), name="alert-reeval"),
    path("incidents/", IncidentListView.as_view(), name="incidents"),
    path("incidents/<int:pk>/", IncidentDetailView.as_view(), name="incident"),
    path("incidents/<int:pk>/rerun/", IncidentRerunView.as_view(), name="incident-rerun"),
    path("incidents/<int:pk>/<str:action>/", IncidentActionView.as_view(), name="incident-action"),
]
