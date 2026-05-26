"""Admin registration for observability models.

This is the secondary operations surface; the primary one is the CLI
(``bin/cli.sh cluster`` → ``manage.py cluster_dest*``). Admin is here so the
project rule that every app provides substantive admin holds, and so
operators can spot-check destination state in a familiar UI.
"""

from django.contrib import admin

from apps.observability.models import ClusterDestination


@admin.register(ClusterDestination)
class ClusterDestinationAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "hub_url",
        "streams",
        "forward_received",
        "is_active",
        "last_push_at",
        "last_push_status",
    ]
    list_filter = ["is_active", "forward_received"]
    search_fields = ["name", "hub_url"]
    readonly_fields = ["last_push_at", "last_push_status", "created_at", "updated_at"]
