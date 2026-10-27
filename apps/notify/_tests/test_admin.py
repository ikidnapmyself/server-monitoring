import pytest
from django.contrib import admin
from django.urls import reverse

from apps.notify.models import NotificationChannel

pytestmark = pytest.mark.django_db


def test_the_channel_admin_edits_but_never_adds(admin_client, rf, admin_user):
    NotificationChannel.objects.create(name="listed", driver="email", config={})
    model_admin = admin.site._registry[NotificationChannel]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["config"]
    assert model_admin.readonly_fields == ["driver"]
    response = admin_client.get(reverse("admin:notify_notificationchannel_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


def test_a_channel_edit_keeps_its_stored_settings(admin_client):
    stored = {"webhook_url": "https://hooks.slack.com/SECRETXYZ"}
    channel = NotificationChannel.objects.create(name="slack", driver="slack", config=stored)
    url = reverse("admin:notify_notificationchannel_change", args=[channel.pk])
    page = admin_client.get(url)
    assert page.status_code == 200
    assert "SECRETXYZ" not in page.content.decode()

    response = admin_client.post(url, {"name": "renamed", "is_active": "on", "description": ""})

    assert response.status_code == 302
    channel.refresh_from_db()
    assert (channel.name, channel.driver, channel.config) == ("renamed", "slack", stored)
