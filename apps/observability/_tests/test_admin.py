"""Tests for observability admin registration."""

from django.contrib import admin


def test_clusterdestination_registered_in_admin():
    from apps.observability.models import ClusterDestination

    assert ClusterDestination in admin.site._registry


def test_admin_list_display_includes_status_fields():
    from apps.observability.models import ClusterDestination

    cfg = admin.site._registry[ClusterDestination]
    for field in (
        "name",
        "hub_url",
        "streams",
        "forward_received",
        "is_active",
        "last_push_at",
        "last_push_status",
    ):
        assert field in cfg.list_display, f"missing {field} from list_display"
