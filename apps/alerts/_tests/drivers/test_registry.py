"""Tests for the alert driver registry."""

from apps.alerts.drivers import DRIVER_REGISTRY
from apps.alerts.drivers.cluster import ClusterDriver
from apps.alerts.drivers.generic import GenericWebhookDriver


def test_cluster_is_always_registered():
    assert DRIVER_REGISTRY.get("cluster") is ClusterDriver


def test_generic_is_registered_last():
    # detect_driver relies on generic being the final fallback.
    assert list(DRIVER_REGISTRY)[-1] == "generic"
    assert DRIVER_REGISTRY["generic"] is GenericWebhookDriver
