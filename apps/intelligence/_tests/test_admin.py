import pytest
from django.contrib import admin
from django.urls import reverse

from apps.intelligence.models import AnalysisRun, IntelligenceProvider

pytestmark = pytest.mark.django_db


def _request(rf, user):
    request = rf.get("/")
    request.user = user
    return request


def test_the_provider_admin_edits_but_never_adds(admin_client, rf, admin_user):
    IntelligenceProvider.objects.create(name="listed", provider="claude")
    model_admin = admin.site._registry[IntelligenceProvider]
    request = _request(rf, admin_user)
    assert not model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["config"]
    assert model_admin.readonly_fields == ["provider"]
    response = admin_client.get(reverse("admin:intelligence_intelligenceprovider_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


def test_a_provider_edit_keeps_its_stored_settings(admin_client):
    stored = {"api_key": "sk-SECRETXYZ"}
    provider = IntelligenceProvider.objects.create(name="main", provider="claude", config=stored)
    url = reverse("admin:intelligence_intelligenceprovider_change", args=[provider.pk])
    page = admin_client.get(url)
    assert page.status_code == 200
    assert "SECRETXYZ" not in page.content.decode()

    response = admin_client.post(url, {"name": "renamed", "description": ""})

    assert response.status_code == 302
    provider.refresh_from_db()
    assert (provider.name, provider.provider, provider.config) == ("renamed", "claude", stored)


def test_the_analysis_admin_is_editable_and_hides_the_provider_config(admin_client, rf, admin_user):
    run = AnalysisRun.objects.create(
        trace_id="t",
        pipeline_run_id="r",
        provider="openai",
        provider_config={"base_url": "https://llm.internal.example"},
    )
    model_admin = admin.site._registry[AnalysisRun]
    request = _request(rf, admin_user)
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["provider_config"]
    assert model_admin.raw_id_fields == ["incident"]
    page = admin_client.get(reverse("admin:intelligence_analysisrun_change", args=[run.pk]))
    assert page.status_code == 200
    assert "llm.internal.example" not in page.content.decode()
