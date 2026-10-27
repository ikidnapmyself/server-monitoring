"""The admin is stock Django for active superusers, and nothing else."""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.console._tests.fixtures import (
    OPS_SLACK_WEBHOOK,
    PROVIDER_CONFIG,
    PROVIDER_SECRET,
    build_world,
)
from apps.intelligence.models import AnalysisRun, IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import InboxItem, PipelineDefinition
from config.admin import MonitoringAdminSite
from config.models import APIKey

pytestmark = pytest.mark.django_db

ADD_REFUSED = {APIKey, IntelligenceProvider, NotificationChannel}
SECRET_FIELDS = {
    NotificationChannel: "config",
    IntelligenceProvider: "config",
    AnalysisRun: "provider_config",
    APIKey: "key",
}


def test_the_default_site_is_the_break_glass_site():
    assert isinstance(admin.site, MonitoringAdminSite)
    assert admin.site.index_template is None
    assert admin.site.site_url == "/console/"


def test_staff_with_every_permission_but_no_superuser_flag_is_turned_away(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    user.user_permissions.set(Permission.objects.all())
    client.force_login(user)

    for url in (
        reverse("admin:index"),
        reverse("admin:notify_notificationchannel_changelist"),
    ):
        resp = client.get(url)
        assert resp.status_code == 302, url
        assert resp["Location"].startswith(reverse("admin:login")), url


def test_an_inactive_superuser_is_turned_away(rf):
    request = rf.get("/admin/")
    request.user = get_user_model()(username="gone", is_superuser=True, is_active=False)

    assert not admin.site.has_permission(request)


def test_a_superuser_gets_the_stock_index(admin_client):
    resp = admin_client.get(reverse("admin:index"))

    assert resp.status_code == 200
    assert resp.templates[0].name == "admin/index.html"
    body = resp.content.decode()
    assert "ops.css" not in body
    assert "Readiness" not in body
    assert reverse("admin:alerts_incident_changelist") in body


def _request(rf, user):
    request = rf.get("/admin/")
    request.user = user
    return request


def test_a_superuser_can_change_and_delete_every_model(rf, admin_user):
    request = _request(rf, admin_user)
    for model, model_admin in admin.site._registry.items():
        assert model_admin.has_change_permission(request), model
        assert model_admin.has_delete_permission(request), model
        assert model_admin.has_add_permission(request) is (model not in ADD_REFUSED), model


def test_no_admin_form_carries_a_secret_field(rf, admin_user):
    request = _request(rf, admin_user)
    for model, field in SECRET_FIELDS.items():
        assert field not in admin.site._registry[model].get_form(request).base_fields, model


def test_the_inbox_proxy_is_not_registered():
    assert not admin.site.is_registered(InboxItem)


def _pages(model, pks):
    name = f"admin:{model._meta.app_label}_{model._meta.model_name}"
    yield reverse(f"{name}_changelist")
    for pk in pks:
        for page in ("change", "history", "delete"):
            yield reverse(f"{name}_{page}", args=[pk])


def test_no_admin_page_shows_a_stored_secret(admin_client):
    world = build_world()
    secrets = [
        OPS_SLACK_WEBHOOK,
        PROVIDER_CONFIG,
        PROVIDER_SECRET,
        *world.key_tokens,
        *APIKey.objects.values_list("key", flat=True),
    ]
    holders = {*SECRET_FIELDS, PipelineDefinition}
    urls = [
        url
        for model in admin.site._registry
        for url in _pages(
            model,
            model._default_manager.values_list("pk", flat=True)[: None if model in holders else 3],
        )
    ]

    for url in urls:
        resp = admin_client.get(url)
        assert resp.status_code == 200, url
        body = resp.content.decode()
        for secret in secrets:
            assert secret not in body, (url, secret)
