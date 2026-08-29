import pytest
from django.test import override_settings
from django.urls import reverse

from config.dashboard import build_readiness, get_dashboard_context


def _by_key(readiness):
    return {r["key"]: r for r in readiness}


@pytest.mark.django_db
def test_channels_error_when_none_active_and_a_lane_promises_delivery():
    """Red because something asked for a channel, not merely because none exists."""
    from apps.notify.models import NotificationChannel
    from apps.orchestration.models import PipelineDefinition

    NotificationChannel.objects.create(name="c1", driver="slack", is_active=False)
    # The seeded lanes omit ``notify`` on a channel-less hub, so the promise has to
    # be made explicitly — that promise is exactly what this entry reports on.
    PipelineDefinition.objects.create(name="delivers", match=[], stages=["notify"], priority=1)
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


# These three describe PEER behaviour, so this instance's own identity is pinned to
# something no fixture uses. Without it local_instance_id() falls back to the host's
# name, and the suite would read its own `agent-*` rows as the self row on a machine
# that happens to be called that.
@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-under-test")
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
@override_settings(INSTANCE_ID="hub-under-test")
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
@override_settings(INSTANCE_ID="hub-under-test")
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


@pytest.mark.django_db
def test_a_lane_bound_to_an_unregistered_driver_is_an_error():
    """The third gap: an active channel is not the same as a deliverable one.

    ``routed_channel()`` answers "is there an active channel", which readiness read
    as "this lane delivers". A channel whose driver is not in DRIVER_REGISTRY —
    removed, or a typo — passed that read while every run on the lane failed
    ``no_driver``. The panel exists so the hub going quiet is diagnosable before an
    incident, so it has to see it.
    """
    from apps.notify.models import NotificationChannel
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    ghost = NotificationChannel.objects.create(name="teams", driver="teams", is_active=True)
    _lane("delivers", ["notify"], channel=ghost)

    entry = _by_key(build_readiness())["lane_channels"]
    assert entry["status"] == "error"
    assert "delivers" in entry["detail"]
    assert "no_driver" in entry["detail"]


@pytest.mark.django_db
def test_a_lane_with_no_channel_still_says_no_channel():
    """Naming which of the two gaps it is, because the fixes differ."""
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("mute", ["notify"])

    assert "no_channel" in _by_key(build_readiness())["lane_channels"]["detail"]


# ---------------------------------------------------------------------------
# Nodes — a peer going quiet is an alarm; this machine's own row is not
# ---------------------------------------------------------------------------
#
# The registry holds two kinds of row since the hub began registering itself.
# For a peer, ``last_seen`` means "still reaching this hub", and its going stale
# is worth waking someone for. For this instance's own row it means only "somebody
# ran a check here", which on a hub checked by hand over SSH is stale nearly all
# the time. Counting the two together made a healthy fleet read amber forever —
# and a panel that is always amber is a panel operators stop reading.


def _stale(node, minutes=30):
    from datetime import timedelta

    from django.utils import timezone

    from apps.alerts.models import Node

    Node.objects.filter(pk=node.pk).update(last_seen=timezone.now() - timedelta(minutes=minutes))


@pytest.mark.django_db
def test_a_stale_self_row_does_not_dim_a_healthy_fleet(settings):
    """The regression: 8 agents all reporting, hub checked by hand, still ok."""
    from apps.alerts.models import Node

    settings.INSTANCE_ID = "hub-1"
    Node.objects.create(instance_id="agent-1")
    Node.objects.create(instance_id="agent-2")
    _stale(Node.objects.create(instance_id="hub-1"), minutes=240)

    entry = _by_key(build_readiness())["nodes"]
    assert entry["status"] == "ok"
    assert "2/2" in entry["detail"]


@pytest.mark.django_db
def test_a_stale_peer_is_still_a_warning(settings):
    """The alarm that matters has to survive the fix."""
    from apps.alerts.models import Node

    settings.INSTANCE_ID = "hub-1"
    Node.objects.create(instance_id="hub-1")
    _stale(Node.objects.create(instance_id="agent-stale"))

    assert _by_key(build_readiness())["nodes"]["status"] == "warn"


@pytest.mark.django_db
def test_the_self_row_is_not_counted_in_the_peer_totals(settings):
    """The numbers in the detail string are peer numbers, self excluded."""
    from apps.alerts.models import Node

    settings.INSTANCE_ID = "hub-1"
    Node.objects.create(instance_id="hub-1")
    Node.objects.create(instance_id="agent-1")

    detail = _by_key(build_readiness())["nodes"]["detail"]
    assert "1/1" in detail
    assert "2/2" not in detail


@pytest.mark.django_db
def test_a_standalone_install_is_not_amber(settings):
    """One machine monitoring itself is a correct configuration, not a fleet in trouble."""
    from apps.alerts.models import Node

    settings.INSTANCE_ID = "hub-1"
    _stale(Node.objects.create(instance_id="hub-1"), minutes=240)

    entry = _by_key(build_readiness())["nodes"]
    assert entry["status"] not in {"warn", "error"}
    assert "No nodes seen" not in entry["detail"]
    assert "standalone" in entry["detail"].lower()


@pytest.mark.django_db
def test_a_fresh_standalone_install_is_not_amber_either(settings):
    """Freshness of the self row changes nothing: it is information, not a verdict."""
    from apps.alerts.models import Node

    settings.INSTANCE_ID = "hub-1"
    Node.objects.create(instance_id="hub-1")

    assert _by_key(build_readiness())["nodes"]["status"] not in {"warn", "error"}


@pytest.mark.django_db
def test_the_self_check_age_is_surfaced_alongside_the_peer_verdict(settings):
    """Information on the card, not an alarm — the operator can see when we last checked."""
    from apps.alerts.models import Node

    settings.INSTANCE_ID = "hub-1"
    Node.objects.create(instance_id="agent-1")
    _stale(Node.objects.create(instance_id="hub-1"), minutes=240)

    assert "self-check" in _by_key(build_readiness())["nodes"]["detail"]


@pytest.mark.django_db
def test_an_empty_registry_is_still_neutral(settings):
    """Nothing has ever reported here — neither an alarm nor a claim of health."""
    settings.INSTANCE_ID = "hub-1"

    entry = _by_key(build_readiness())["nodes"]
    assert entry["status"] == "neutral"
    assert entry["detail"] == "No nodes seen"


@pytest.mark.django_db
def test_no_peers_seen_recently_still_warns(settings):
    """Every peer gone quiet keeps its own wording, self row or not."""
    from apps.alerts.models import Node

    settings.INSTANCE_ID = "hub-1"
    Node.objects.create(instance_id="hub-1")
    _stale(Node.objects.create(instance_id="agent-a"))
    _stale(Node.objects.create(instance_id="agent-b"))

    entry = _by_key(build_readiness())["nodes"]
    assert entry["status"] == "warn"
    assert "No node seen in" in entry["detail"]
