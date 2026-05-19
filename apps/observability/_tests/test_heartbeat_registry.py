"""Tests for HEARTBEAT_REGISTRY."""

from datetime import timedelta

from apps.observability.heartbeat_registry import (
    HEARTBEAT_REGISTRY,
    HeartbeatSpec,
)


def test_known_specs_are_registered():
    assert "check_health.hourly" in HEARTBEAT_REGISTRY
    assert "check_health.daily" in HEARTBEAT_REGISTRY
    assert "push_to_hub" in HEARTBEAT_REGISTRY
    assert "cluster_push.events" in HEARTBEAT_REGISTRY
    assert "preflight.scheduled" in HEARTBEAT_REGISTRY


def test_spec_shape():
    spec = HEARTBEAT_REGISTRY["check_health.hourly"]
    assert isinstance(spec, HeartbeatSpec)
    assert isinstance(spec.max_age, timedelta)
    assert spec.max_age.total_seconds() > 0
    assert spec.desc


def test_agent_only_flag_present_on_push_jobs():
    assert HEARTBEAT_REGISTRY["push_to_hub"].agent_only is True
    assert HEARTBEAT_REGISTRY["cluster_push.events"].agent_only is True
    assert HEARTBEAT_REGISTRY["check_health.hourly"].agent_only is False
