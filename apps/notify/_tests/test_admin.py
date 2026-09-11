"""The channel admin's view back into routing.

Deactivating a channel or renaming its driver breaks every lane bound to it, and
until this panel existed the page that does the breaking never named them. The
dashboard already knew the answer via ``delivery_gap()``; the channel did not.
"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.urls import reverse

from apps.notify.admin import NotificationChannelAdmin
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db


@pytest.fixture
def model_admin():
    return NotificationChannelAdmin(NotificationChannel, AdminSite())


@pytest.fixture
def channel():
    clear_lanes()
    return NotificationChannel.objects.create(name="ops", driver="email", is_active=True, config={})


def _lane(name, channel, priority=1):
    return PipelineDefinition.objects.create(
        name=name,
        priority=priority,
        match=[{"field": "source", "op": "is", "value": name}],
        stages=["notify"],
        channel=channel,
    )


def _listed(model_admin, rf, admin_user, channel):
    """The channel as the changelist sees it, annotation included."""
    request = rf.get("/")
    request.user = admin_user
    return model_admin.get_queryset(request).get(pk=channel.pk)


def test_the_count_links_the_lanes_bound_to_this_channel(
    model_admin, channel, admin_client, rf, admin_user
):
    _lane("a", channel)
    _lane("b", channel, priority=2)
    html = str(model_admin.lane_count(_listed(model_admin, rf, admin_user, channel)))
    url = reverse("admin:orchestration_pipelinedefinition_changelist")
    expected = f"{url}?channel__id__exact={channel.pk}"
    assert f'<a href="{expected}">2</a>' == html
    assert admin_client.get(expected).status_code == 200


def test_a_channel_nothing_routes_to_is_a_plain_zero(model_admin, channel, rf, admin_user):
    assert model_admin.lane_count(_listed(model_admin, rf, admin_user, channel)) == 0


def test_the_panel_names_each_lane_and_links_it(model_admin, channel):
    lane = _lane("a", channel)
    html = str(model_admin.lanes_display(channel))
    url = reverse("admin:orchestration_pipelinedefinition_change", args=[lane.pk])
    assert f'<a href="{url}">a</a>' in html


def test_the_panel_says_so_when_nothing_routes_here(model_admin, channel):
    assert "No lane routes here" in str(model_admin.lanes_display(channel))


def test_an_unsaved_channel_has_no_lanes_to_show(model_admin):
    assert "No lane routes here" in str(model_admin.lanes_display(NotificationChannel()))


def test_the_panel_marks_a_lane_that_would_stop_delivering(model_admin, channel):
    """An inactive channel is the reason those lanes cannot deliver, and this is
    the page where that was done."""
    _lane("a", channel)
    channel.is_active = False
    channel.save(update_fields=["is_active"])
    assert "cannot deliver" in str(model_admin.lanes_display(channel))


def _queries_for(client, url):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client.get(url)  # the first request warms session and content-type caches
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return len(ctx.captured_queries)


def test_the_changelist_count_does_not_cost_a_query_per_channel(admin_client, channel):
    """The count comes from one annotated query, not one lane load per row."""
    _lane("a", channel)
    url = reverse("admin:notify_notificationchannel_changelist")
    one = _queries_for(admin_client, url)
    for i in range(3):
        extra = NotificationChannel.objects.create(
            name=f"extra-{i}", driver="email", is_active=True, config={}
        )
        _lane(f"lane-{i}", extra, priority=10 + i)
    assert _queries_for(admin_client, url) == one


def test_the_panel_does_not_cost_a_query_per_lane(admin_client, channel):
    """delivery_gap reads lane.channel, so the lanes must arrive with it joined."""
    _lane("a", channel)
    url = reverse("admin:notify_notificationchannel_change", args=[channel.pk])
    one = _queries_for(admin_client, url)
    for i in range(3):
        _lane(f"more-{i}", channel, priority=10 + i)
    assert _queries_for(admin_client, url) == one
