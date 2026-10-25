import pytest
from django.contrib import admin
from django.db.models import JSONField
from django.urls import reverse

from apps.intelligence.models import AnalysisRun, IntelligenceProvider

pytestmark = pytest.mark.django_db


def test_the_provider_admin_is_stock_and_read_only(admin_client, rf, admin_user):
    IntelligenceProvider.objects.create(name="listed", provider="claude")
    model_admin = admin.site._registry[IntelligenceProvider]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    assert model_admin.list_display == ["name", "provider", "is_active", "updated_at"]
    assert model_admin.exclude == ["config"]
    response = admin_client.get(reverse("admin:intelligence_intelligenceprovider_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


def test_the_provider_admin_never_shows_stored_settings(admin_client):
    provider = IntelligenceProvider.objects.create(
        name="main", provider="claude", config={"api_key": "sk-SECRETXYZ"}
    )
    response = admin_client.get(
        reverse("admin:intelligence_intelligenceprovider_change", args=[provider.pk])
    )
    assert response.status_code == 200
    assert "SECRETXYZ" not in response.content.decode()


def test_the_analysis_admin_is_stock_and_read_only(admin_client, rf, admin_user):
    run = AnalysisRun.objects.create(
        trace_id="t",
        pipeline_run_id="r",
        provider="openai",
        provider_config={"base_url": "https://llm.internal.example"},
    )
    model_admin = admin.site._registry[AnalysisRun]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    assert model_admin.list_display == ["created_at", "provider", "status", "total_tokens"]
    assert model_admin.exclude == ["provider_config"]
    assert model_admin.formfield_overrides.get(JSONField, {}).get("widget") is None
    assert admin_client.get(reverse("admin:intelligence_analysisrun_changelist")).status_code == 200
    page = admin_client.get(reverse("admin:intelligence_analysisrun_change", args=[run.pk]))
    assert page.status_code == 200
    assert "llm.internal.example" not in page.content.decode()
