from django.urls import path

from apps.console.views import (
    AlertDetailView,
    AlertListView,
    AlertReevalView,
    IncidentActionView,
    IncidentDetailView,
    IncidentListView,
    IncidentRerunView,
    OverviewView,
    RunActionView,
    RunDetailView,
    RunListView,
)

app_name = "console"

urlpatterns = [
    path("", OverviewView.as_view(), name="overview"),
    path("alerts/", AlertListView.as_view(), name="alerts"),
    path("alerts/<int:pk>/", AlertDetailView.as_view(), name="alert"),
    path("alerts/<int:pk>/reevaluate/", AlertReevalView.as_view(), name="alert-reeval"),
    path("runs/", RunListView.as_view(), name="runs"),
    path("runs/<int:pk>/", RunDetailView.as_view(), name="run"),
    path("runs/<int:pk>/<str:action>/", RunActionView.as_view(), name="run-action"),
    path("incidents/", IncidentListView.as_view(), name="incidents"),
    path("incidents/<int:pk>/", IncidentDetailView.as_view(), name="incident"),
    path("incidents/<int:pk>/rerun/", IncidentRerunView.as_view(), name="incident-rerun"),
    path("incidents/<int:pk>/<str:action>/", IncidentActionView.as_view(), name="incident-action"),
]
