import pytest
from django.urls import reverse

from config.dashboard import build_readiness, get_dashboard_context


def _by_key(readiness):
    return {r["key"]: r for r in readiness}


@pytest.mark.django_db
def test_channels_error_when_none_active():
    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="c1", driver="slack", is_active=False)
    r = _by_key(build_readiness())["channels"]
    assert r["status"] == "error"
    assert r["url"] == reverse("admin:notify_notificationchannel_changelist")


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
