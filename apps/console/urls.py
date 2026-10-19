from django.urls import path

from apps.console.views import (
    AlertDetailView,
    AlertListView,
    AlertReevalView,
    InboxActionView,
    InboxPageView,
    IncidentActionView,
    IncidentDetailView,
    IncidentListView,
    IncidentRerunView,
    NodeDetailView,
    NodeListView,
    NodePolicyView,
    NodeReevalView,
    OverviewView,
    PolicyView,
    RouteDeleteView,
    RoutesView,
    RouteView,
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
    path("inbox/", InboxPageView.as_view(), name="inbox"),
    path(
        "inbox/drain-oldest/",
        InboxActionView.as_view(),
        {"action": "drain-oldest"},
        name="inbox-drain-oldest",
    ),
    path("inbox/reclaim/", InboxActionView.as_view(), {"action": "reclaim"}, name="inbox-reclaim"),
    path(
        "inbox/<int:pk>/drain/", InboxActionView.as_view(), {"action": "drain"}, name="inbox-drain"
    ),
    path("nodes/", NodeListView.as_view(), name="nodes"),
    path("nodes/<int:pk>/", NodeDetailView.as_view(), name="node"),
    path("nodes/<int:pk>/policy/", NodePolicyView.as_view(), name="node-policy"),
    path("nodes/<int:pk>/reevaluate/", NodeReevalView.as_view(), name="node-reeval"),
    path("policy/", PolicyView.as_view(), name="policy"),
    path("routes/", RoutesView.as_view(), name="routes"),
    path("routes/new/", RouteView.as_view(), name="route-new"),
    path("routes/<int:pk>/", RouteView.as_view(), name="route"),
    path("routes/<int:pk>/delete/", RouteDeleteView.as_view(), name="route-delete"),
    path("incidents/", IncidentListView.as_view(), name="incidents"),
    path("incidents/<int:pk>/", IncidentDetailView.as_view(), name="incident"),
    path("incidents/<int:pk>/rerun/", IncidentRerunView.as_view(), name="incident-rerun"),
    path("incidents/<int:pk>/<str:action>/", IncidentActionView.as_view(), name="incident-action"),
]
