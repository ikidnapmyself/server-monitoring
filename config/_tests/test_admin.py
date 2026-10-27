"""The admin is stock Django for active superusers, and nothing else."""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from config.admin import MonitoringAdminSite

pytestmark = pytest.mark.django_db


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
