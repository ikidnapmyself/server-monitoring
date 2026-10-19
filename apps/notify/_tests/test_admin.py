import pytest
from django.contrib import admin
from django.urls import reverse

from apps.notify.models import NotificationChannel

pytestmark = pytest.mark.django_db


def test_the_channel_admin_is_stock_and_read_only(admin_client, rf, admin_user):
    NotificationChannel.objects.create(name="listed", driver="email", config={})
    model_admin = admin.site._registry[NotificationChannel]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    response = admin_client.get(reverse("admin:notify_notificationchannel_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


def test_the_channel_admin_never_shows_stored_settings(admin_client):
    channel = NotificationChannel.objects.create(
        name="slack", driver="slack", config={"webhook_url": "https://hooks.slack.com/SECRETXYZ"}
    )
    response = admin_client.get(
        reverse("admin:notify_notificationchannel_change", args=[channel.pk])
    )
    assert response.status_code == 200
    assert "SECRETXYZ" not in response.content.decode()
