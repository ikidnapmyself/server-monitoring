import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory


@pytest.fixture
def su(db):
    return get_user_model().objects.create_superuser("admin", "a@b.co", "x")


def _sections(su):
    req = RequestFactory().get("/admin/")
    req.user = su
    return {
        a["name"]: [m["object_name"] for m in a["models"]] for a in admin.site.get_app_list(req)
    }


@pytest.mark.django_db
def test_sections_are_operator_facing(su):
    sections = _sections(su)
    assert set(sections) >= {"Operations", "Configuration", "History & Audit"}
    assert "Incident" in sections["Operations"]
    assert "Node" in sections["Operations"]
    assert "NotificationChannel" in sections["Configuration"]
    assert "CheckRun" in sections["History & Audit"]


@pytest.mark.django_db
def test_operations_order_is_explicit(su):
    ops = _sections(su)["Operations"]
    assert ops.index("Incident") < ops.index("Alert") < ops.index("PipelineRun")


@pytest.mark.django_db
def test_all_models_present_none_dropped(su):
    all_models = [m for models in _sections(su).values() for m in models]
    # auth models are in Configuration; nothing silently dropped
    assert "User" in all_models and "Group" in all_models
    assert "StageExecution" in all_models and "AnalysisRun" in all_models


@pytest.mark.django_db
def test_per_app_label_preserves_native_behaviour(su):
    # When app_label is passed (per-app index), do NOT regroup.
    req = RequestFactory().get("/admin/alerts/")
    req.user = su
    result = admin.site.get_app_list(req, app_label="alerts")
    assert [a["app_label"] for a in result] == ["alerts"]
