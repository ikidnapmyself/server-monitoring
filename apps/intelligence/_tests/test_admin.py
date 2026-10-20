import pytest
from django.contrib import admin
from django.urls import reverse

from apps.intelligence.models import IntelligenceProvider

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
