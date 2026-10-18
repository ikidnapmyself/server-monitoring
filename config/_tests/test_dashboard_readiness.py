import pytest
from django.test import override_settings
from django.urls import reverse

from config.dashboard import (
    build_readiness,
    get_dashboard_context,
    keyed_readiness,
)


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
    # A fault goes to the map, which names the gap per lane — see
    # test_an_undeliverable_lane_points_at_the_map_which_names_the_gap.
    assert entry["url"] == reverse("admin:netmap")


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


@pytest.mark.django_db
def test_the_inbox_entry_filters_to_the_runs_it_counted():
    """A count that names pending or stuck work has to land on that work."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.orchestration.models import PipelineRun, PipelineStatus

    base = reverse("console:inbox")
    assert _by_key(build_readiness())["inbox"]["url"] == base

    PipelineRun.objects.create(trace_id="t", run_id="p1", status=PipelineStatus.PENDING)
    assert _by_key(build_readiness())["inbox"]["url"] == f"{base}?status=pending"

    run = PipelineRun.objects.create(trace_id="t", run_id="p2", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    assert _by_key(build_readiness())["inbox"]["url"] == f"{base}?status=processing"


@pytest.mark.django_db
def test_an_undeliverable_lane_points_at_the_map_which_names_the_gap():
    """The lane list cannot show a delivery gap; the map shows it per lane."""
    from apps.orchestration.models import PipelineDefinition

    PipelineDefinition.objects.create(
        name="promises",
        priority=1,
        match=[],
        stages=["notify"],
        is_active=True,
    )
    entry = _by_key(build_readiness())["lane_channels"]
    assert entry["status"] == "error"
    assert entry["url"] == reverse("admin:netmap")


# ---------------------------------------------------------------------------
# counts and gaps: the numbers each entry computed, kept for describers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_entry_carries_counts_and_gaps():
    for entry in build_readiness():
        assert isinstance(entry["counts"], dict)
        assert isinstance(entry["gaps"], list)


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-under-test")
def test_counts_hold_the_numbers_behind_each_entry():
    from datetime import timedelta

    from django.utils import timezone

    from apps.alerts.models import Node
    from apps.checkers.models import PreflightRun
    from apps.orchestration.models import PipelineRun, PipelineStatus
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _channel("on")
    _channel("off", is_active=False)
    _lane("delivers", ["notify"])
    PreflightRun.objects.create(overall_status="error", passed=4, warnings=1, errors=2)
    PipelineRun.objects.create(trace_id="t", run_id="p1", status=PipelineStatus.PENDING)
    run = PipelineRun.objects.create(trace_id="t", run_id="p2", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    Node.objects.create(instance_id="agent-1")
    _stale(Node.objects.create(instance_id="agent-2"))

    r = _by_key(build_readiness())
    assert r["channels"]["counts"] == {"active": 1, "total": 2, "delivering": 1}
    assert r["lane_channels"]["counts"] == {"delivering": 1}
    assert r["lane_channels"]["gaps"] == [("delivers", "no_channel")]
    assert r["channels"]["gaps"] == []
    assert r["provider"]["counts"] == {"active": 0}
    assert r["preflight"]["counts"] == {"passed": 4, "warnings": 1, "errors": 2}
    assert r["inbox"]["counts"] == {"pending": 1, "stuck": 1}
    assert r["nodes"]["counts"] == {"total": 2, "recent": 1}


@pytest.mark.django_db
def test_preflight_never_run_counts_nothing():
    assert _by_key(build_readiness())["preflight"]["counts"] == {}


# ---------------------------------------------------------------------------
# keyed_readiness: one Description per entry that needs a fix
# ---------------------------------------------------------------------------


def _green_install():
    from apps.intelligence.models import IntelligenceProvider
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("delivers", ["notify"], channel=_channel())
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)


def _described():
    return tuple(d for _, d in keyed_readiness())


def _headlines(found):
    return [d.headline for d in found]


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-under-test")
def test_an_all_green_install_describes_nothing():
    from apps.alerts.models import Node
    from apps.checkers.models import PreflightRun

    _green_install()
    PreflightRun.objects.create(overall_status="ok", passed=5)
    Node.objects.create(instance_id="agent-1")

    assert _described() == ()


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-under-test")
def test_info_and_neutral_entries_describe_nothing():
    from apps.intelligence.models import IntelligenceProvider
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("records", ["analyze"])
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)

    statuses = {e["key"]: e["status"] for e in build_readiness()}
    assert statuses["channels"] == "info"
    assert statuses["preflight"] == "neutral"
    assert _described() == ()


@pytest.mark.django_db
def test_no_channel_with_delivering_lanes_names_the_fix():
    from apps.intelligence.models import IntelligenceProvider
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)
    _lane("catch-all", ["notify"])
    _lane("paging", ["check", "notify"])

    first = _described()[0]
    assert first.headline == "No active channel, 2 routes can't deliver"
    assert first.tone == "critical"
    assert first.action.intent == "edit_channel"


@pytest.mark.django_db
def test_one_route_reads_singular():
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("catch-all", ["notify"])

    assert _described()[0].headline == "No active channel, 1 route can't deliver"


@pytest.mark.django_db
def test_a_lane_gap_reads_from_the_shared_reasons():
    from apps.intelligence.models import IntelligenceProvider
    from apps.orchestration.describing import REASONS
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)
    _channel()
    _lane("catch-all", ["notify"])

    (gap,) = _described()
    assert gap.headline == (
        "Route catch-all can't deliver: the matched route has no active channel"
    )
    assert gap.tone == "critical"
    assert gap.action == REASONS["no_channel"].action


@pytest.mark.django_db
def test_several_gaps_describe_one_each():
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.describing import REASONS
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)
    ghost = NotificationChannel.objects.create(name="teams", driver="teams", is_active=True)
    _lane("alpha", ["notify"], channel=ghost)
    _lane("beta", ["notify"])

    found = _described()
    assert _headlines(found) == [
        f"Route alpha can't deliver: {REASONS['no_driver'].headline}",
        f"Route beta can't deliver: {REASONS['no_channel'].headline}",
    ]
    assert [d.action for d in found] == [REASONS["no_driver"].action, REASONS["no_channel"].action]


@pytest.mark.django_db
def test_no_provider_says_analysis_is_skipped():
    from apps.orchestration.testing import clear_lanes

    clear_lanes()

    (found,) = _described()
    assert found.headline == "No AI provider is active, analysis is skipped"
    assert found.tone == "critical"
    assert found.action.intent == "edit_provider"


@pytest.mark.parametrize(
    "status,counts,headline,tone",
    [
        ("error", {"errors": 2}, "Preflight found 2 errors", "critical"),
        ("error", {"errors": 1}, "Preflight found 1 error", "critical"),
        ("warn", {"warnings": 3}, "Preflight found 3 warnings", "warning"),
        ("warn", {"warnings": 1}, "Preflight found 1 warning", "warning"),
        ("error", {}, "Preflight didn't pass", "critical"),
        ("warn", {}, "Preflight didn't pass", "warning"),
    ],
)
@pytest.mark.django_db
def test_preflight_counts_what_it_found(status, counts, headline, tone):
    from apps.checkers.models import PreflightRun

    _green_install()
    PreflightRun.objects.create(overall_status=status, **counts)

    (found,) = _described()
    assert found.headline == headline
    assert found.tone == tone
    assert found.action.intent == "view_preflight"


@pytest.mark.parametrize(
    "pending,stuck,headline,status,tone",
    [
        (0, 3, "3 runs are stuck", "processing", "critical"),
        (0, 1, "1 run is stuck", "processing", "critical"),
        (4, 0, "4 runs are waiting to be processed", "pending", "warning"),
        (1, 0, "1 run is waiting to be processed", "pending", "warning"),
    ],
)
@pytest.mark.django_db
def test_inbox_names_the_runs_and_filters_to_them(pending, stuck, headline, status, tone):
    from datetime import timedelta

    from django.utils import timezone

    from apps.orchestration.models import PipelineRun, PipelineStatus

    _green_install()
    for i in range(pending):
        PipelineRun.objects.create(trace_id="t", run_id=f"p{i}", status=PipelineStatus.PENDING)
    for i in range(stuck):
        run = PipelineRun.objects.create(
            trace_id="t", run_id=f"s{i}", status=PipelineStatus.PROCESSING
        )
        PipelineRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))

    (found,) = _described()
    assert found.headline == headline
    assert found.tone == tone
    assert found.action.intent == "view_inbox"
    assert found.action.params == {"status": status}


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-under-test")
def test_some_silent_peers_are_counted():
    from apps.alerts.models import Node

    _green_install()
    for i in range(3):
        Node.objects.create(instance_id=f"agent-{i}")
    _stale(Node.objects.create(instance_id="agent-x"))
    _stale(Node.objects.create(instance_id="agent-y"))

    (found,) = _described()
    assert found.headline == "2 of 5 nodes stopped reporting"
    assert found.tone == "warning"
    assert found.action.intent == "view_nodes"


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-under-test")
def test_every_peer_silent_names_the_window():
    from apps.alerts.models import Node
    from config.dashboard import NODE_RECENT_MINUTES

    _green_install()
    _stale(Node.objects.create(instance_id="agent-a"))

    (found,) = _described()
    assert found.headline == f"No node reported in the last {NODE_RECENT_MINUTES} minutes"
    assert found.action.intent == "view_nodes"


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-under-test")
def test_descriptions_follow_readiness_order_and_hold_no_tokens():
    import re
    from datetime import timedelta

    from django.utils import timezone

    from apps.alerts.models import Node
    from apps.checkers.models import PreflightRun
    from apps.orchestration.models import PipelineRun, PipelineStatus
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    _lane("catch-all", ["notify"])
    PreflightRun.objects.create(overall_status="error", errors=2)
    run = PipelineRun.objects.create(trace_id="t", run_id="s", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    _stale(Node.objects.create(instance_id="agent-a"))

    assert [key for key, _ in keyed_readiness()] == [
        "channels",
        "provider",
        "preflight",
        "inbox",
        "nodes",
    ]
    found = _described()
    assert [d.action.intent for d in found] == [
        "edit_channel",
        "edit_provider",
        "view_preflight",
        "view_inbox",
        "view_nodes",
    ]
    for d in found:
        for text in (d.headline, d.detail, d.action.text):
            assert not re.search(r"\b[a-z]+_[a-z_]+\b", text), text
            assert "{" not in text


@pytest.mark.django_db
def test_no_channel_at_all_is_said_once_not_per_lane():
    from apps.intelligence.models import IntelligenceProvider
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)
    _lane("alpha", ["notify"])
    _lane("beta", ["notify"])

    assert _headlines(_described()) == ["No active channel, 2 routes can't deliver"]


@pytest.mark.django_db
def test_a_driver_gap_is_kept_when_the_channel_entry_fired(monkeypatch):
    from apps.orchestration.describing import REASONS
    from config import dashboard

    entries = [
        {
            "key": "channels",
            "label": "Notification channels",
            "status": "error",
            "gaps": [],
            "counts": {"active": 0, "total": 0, "delivering": 2},
        },
        {
            "key": "lane_channels",
            "label": "Lane delivery",
            "status": "error",
            "gaps": [("alpha", "no_driver"), ("beta", "no_channel")],
            "counts": {"delivering": 2},
        },
    ]
    monkeypatch.setattr(dashboard, "build_readiness", lambda: entries)

    assert _headlines(_described()) == [
        "No active channel, 2 routes can't deliver",
        f"Route alpha can't deliver: {REASONS['no_driver'].headline}",
    ]


def test_an_unknown_entry_gets_a_generic_description(monkeypatch):
    from config import dashboard

    monkeypatch.setattr(
        dashboard,
        "build_readiness",
        lambda: [
            {"key": "backups", "label": "Backups", "status": "warn", "gaps": [], "counts": {}},
            {"key": "certs", "label": "Certificates", "status": "error", "gaps": [], "counts": {}},
        ],
    )

    found = _described()
    assert _headlines(found) == ["Backups needs attention", "Certificates needs attention"]
    assert [d.tone for d in found] == ["warning", "critical"]
    assert all(d.action is None for d in found)


@pytest.mark.django_db
def test_a_lane_gap_is_keyed_by_its_lane_as_a_valid_id():
    from apps.intelligence.models import IntelligenceProvider
    from apps.orchestration.testing import clear_lanes

    clear_lanes()
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)
    _channel()
    _lane("Paging Lane/EU", ["notify"])
    _lane("beta", ["notify"])

    assert [key for key, _ in keyed_readiness()] == [
        "lane_channels-paging-laneeu",
        "lane_channels-beta",
    ]


def test_an_entry_is_keyed_by_its_own_key(monkeypatch):
    from config import dashboard

    monkeypatch.setattr(
        dashboard,
        "build_readiness",
        lambda: [{"key": "certs", "label": "Certificates", "status": "error", "gaps": []}],
    )

    ((key, found),) = keyed_readiness()
    assert key == "certs"
    assert found.headline == "Certificates needs attention"
