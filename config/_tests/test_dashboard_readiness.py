import pytest
from django.urls import reverse

from config.dashboard import build_readiness, get_dashboard_context


def _by_key(readiness):
    return {r["key"]: r for r in readiness}


@pytest.mark.django_db
def test_channels_error_when_none_active_and_a_lane_promises_delivery():
    """Red because something asked for a channel, not merely because none exists."""
    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="c1", driver="slack", is_active=False)
    r = _by_key(build_readiness())["channels"]
    assert r["status"] == "error"
    assert r["url"] == reverse("admin:notify_notificationchannel_changelist")


@pytest.mark.django_db
def test_channels_info_when_no_channel_and_no_lane_delivers():
    """A hub that reads the admin daily and notifies nobody is not broken.

    Painting it red is how a readiness panel becomes something operators learn to
    ignore, which costs them the entries that do matter.
    """
    from apps.orchestration.models import PipelineDefinition
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    PipelineDefinition.objects.create(
        name="records", match=[], stages=["check", "analyze"], priority=1
    )

    r = _by_key(build_readiness())["channels"]
    assert r["status"] == "info"
    assert "recording only" in r["detail"].lower()


@pytest.mark.django_db
def test_channels_ok_when_some_inactive():
    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="a", driver="slack", is_active=True)
    NotificationChannel.objects.create(name="b", driver="email", is_active=False)
    assert _by_key(build_readiness())["channels"]["status"] == "ok"


@pytest.mark.django_db
def test_channels_ok_when_all_active():
    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="a", driver="slack", is_active=True)
    assert _by_key(build_readiness())["channels"]["status"] == "ok"


@pytest.mark.django_db
def test_provider_error_when_none_active_else_ok():
    from apps.intelligence.models import IntelligenceProvider

    assert _by_key(build_readiness())["provider"]["status"] == "error"
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)
    assert _by_key(build_readiness())["provider"]["status"] == "ok"


@pytest.mark.django_db
def test_preflight_neutral_then_maps_status():
    from apps.checkers.models import PreflightRun

    assert _by_key(build_readiness())["preflight"]["status"] == "neutral"
    PreflightRun.objects.create(overall_status="warn")
    assert _by_key(build_readiness())["preflight"]["status"] == "warn"


@pytest.mark.django_db
def test_inbox_ok_backlog_stuck():
    from datetime import timedelta

    from django.utils import timezone

    from apps.orchestration.models import PipelineRun, PipelineStatus

    assert _by_key(build_readiness())["inbox"]["status"] == "ok"
    PipelineRun.objects.create(trace_id="t", run_id="p1", status=PipelineStatus.PENDING)
    assert _by_key(build_readiness())["inbox"]["status"] == "warn"
    run = PipelineRun.objects.create(trace_id="t", run_id="p2", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    assert _by_key(build_readiness())["inbox"]["status"] == "error"


@pytest.mark.django_db
def test_nodes_neutral_then_recent_ok():
    from apps.alerts.models import Node

    assert _by_key(build_readiness())["nodes"]["status"] == "neutral"
    Node.objects.create(instance_id="agent-1")
    assert _by_key(build_readiness())["nodes"]["status"] == "ok"


@pytest.mark.django_db
def test_readiness_in_dashboard_context():
    ctx = get_dashboard_context()
    assert "readiness" in ctx
    readiness = ctx["readiness"]
    assert isinstance(readiness, list) and readiness
    for entry in readiness:
        assert {"key", "label", "status", "detail", "url"} <= set(entry)
    keys = {entry["key"] for entry in readiness}
    assert {"channels", "provider", "preflight", "inbox", "nodes"} <= keys


@pytest.mark.django_db
def test_nodes_warn_when_stale():
    from datetime import timedelta

    from django.utils import timezone

    from apps.alerts.models import Node

    node = Node.objects.create(instance_id="agent-stale")
    Node.objects.filter(pk=node.pk).update(last_seen=timezone.now() - timedelta(minutes=30))
    assert _by_key(build_readiness())["nodes"]["status"] == "warn"


@pytest.mark.django_db
def test_preflight_unknown_status_is_neutral():
    from apps.checkers.models import PreflightRun

    PreflightRun.objects.create(overall_status="unknown")
    assert _by_key(build_readiness())["preflight"]["status"] == "neutral"


@pytest.mark.django_db
def test_nodes_warn_when_some_stale():
    from datetime import timedelta

    from django.utils import timezone

    from apps.alerts.models import Node

    Node.objects.create(instance_id="agent-fresh")
    stale = Node.objects.create(instance_id="agent-partial-stale")
    Node.objects.filter(pk=stale.pk).update(last_seen=timezone.now() - timedelta(minutes=30))
    assert _by_key(build_readiness())["nodes"]["status"] == "warn"


# ---------------------------------------------------------------------------
# Lane delivery — three states, and only one of them is red
# ---------------------------------------------------------------------------
#
# A lane that lists ``notify`` promises delivery. Whether it can keep that
# promise is ``routed_channel()``, and nothing else. The states that follow are
# the difference between a hub that is misconfigured and a hub that simply never
# claimed to deliver. See docs/plans/2026-08-22-lane-channel-required-design.md
# §2.3.


def _lane(name, stages, channel=None, is_active=True):
    from apps.orchestration.models import PipelineDefinition

    return PipelineDefinition.objects.create(
        name=name, match=[], stages=stages, priority=1, channel=channel, is_active=is_active
    )


def _channel(name="ops", is_active=True):
    from apps.notify.models import NotificationChannel

    return NotificationChannel.objects.create(
        name=name, driver="generic", is_active=is_active, config={}
    )


@pytest.mark.django_db
def test_lane_that_claims_to_deliver_but_cannot_is_an_error():
    """The only red state: a row says NOTIFY and delivery has nowhere to go."""
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("mute", ["notify"])

    entry = _by_key(build_readiness())["lane_channels"]
    assert entry["status"] == "error"
    assert "mute" in entry["detail"]
    assert entry["url"] == reverse("admin:orchestration_pipelinedefinition_changelist")


@pytest.mark.django_db
def test_a_recording_hub_is_not_an_error():
    """No channel and no delivering lane is a supported setup, not a fault.

    An operator who reads the admin daily and runs no Slack must not see red.
    """
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("records", ["analyze"])

    assert _by_key(build_readiness())["lane_channels"]["status"] == "info"


@pytest.mark.django_db
def test_every_delivering_lane_bound_is_ok():
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("delivers", ["notify"], channel=_channel())

    entry = _by_key(build_readiness())["lane_channels"]
    assert entry["status"] == "ok"
    assert "1" in entry["detail"]


@pytest.mark.django_db
def test_an_inactive_channel_on_a_lane_is_no_channel():
    """routed_channel() is the one rule for "active"; readiness re-derives nothing."""
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("delivers", ["notify"], channel=_channel(is_active=False))

    assert _by_key(build_readiness())["lane_channels"]["status"] == "error"


@pytest.mark.django_db
def test_a_channel_nobody_delivers_to_is_a_nudge_not_an_alarm():
    """One edit away from delivering — say so, do not shout."""
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _channel()
    _lane("records", ["analyze"])

    entry = _by_key(build_readiness())["lane_channels"]
    assert entry["status"] == "ok"
    assert "no lane" in entry["detail"].lower()


@pytest.mark.django_db
def test_a_lane_that_never_notifies_is_not_reported():
    """hub-self-check lists no stages; it is not broken, it is quiet by design."""
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("hub-self-check", [])

    entry = _by_key(build_readiness())["lane_channels"]
    assert entry["status"] == "info"
    assert "hub-self-check" not in entry["detail"]


@pytest.mark.django_db
def test_an_inactive_lane_cannot_fail_so_it_is_not_reported():
    """A lane that never runs delivers nothing; red would be noise."""
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("disabled", ["notify"], is_active=False)

    assert _by_key(build_readiness())["lane_channels"]["status"] == "info"
