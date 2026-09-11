"""Admin configuration for notify models."""

from django.contrib import admin
from django.db import models as db_models
from django.db.models import Count
from django.utils.html import format_html, format_html_join
from django_json_widget.widgets import JSONEditorWidget

from apps.notify.models import NotificationChannel
from config.admin_links import admin_link, changelist_link


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    """Admin for NotificationChannel model."""

    list_display = [
        "name",
        "driver",
        "is_active",
        "lane_count",
        "created_at",
        "updated_at",
    ]
    formfield_overrides = {db_models.JSONField: {"widget": JSONEditorWidget}}
    readonly_fields = ["created_at", "updated_at", "lanes_display"]
    list_filter = ["driver", "is_active"]
    search_fields = ["name", "description"]
    fieldsets = [
        (
            None,
            {
                "fields": ["name", "driver", "is_active", "description", "lanes_display"],
            },
        ),
        (
            "Configuration",
            {
                "fields": ["config"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]

    def get_queryset(self, request):
        """Count each channel's lanes in the list query, not once per row."""
        return super().get_queryset(request).annotate(lane_total=Count("pipelines"))

    def _lanes(self, obj):
        """Lanes bound to this channel, with the channel joined.

        ``delivery_gap`` reads ``lane.channel``, so without the join every lane
        costs another query. Imported here rather than at module scope:
        ``apps.orchestration`` imports notify's drivers, so a top-level import
        closes a cycle.
        """
        from apps.orchestration.models import PipelineDefinition

        if obj.pk is None:
            return []
        return list(
            PipelineDefinition.objects.filter(channel_id=obj.pk)
            .select_related("channel")
            .order_by("priority", "id")
        )

    @admin.display(description="Lanes", ordering="lane_total")
    def lane_count(self, obj):
        """How many lanes route here, linked to exactly those lanes."""
        from apps.orchestration.models import PipelineDefinition

        if not obj.lane_total:
            return obj.lane_total
        return changelist_link(PipelineDefinition, obj.lane_total, channel__id__exact=obj.pk)

    @admin.display(description="Routed here by")
    def lanes_display(self, obj):
        """The lanes this channel serves, each with whether it can still deliver.

        Editing a channel is how delivery breaks, so the page that edits it names
        what it would break. ``delivery_gap`` is the one rule for that, asked
        here rather than re-derived.
        """
        lanes = self._lanes(obj)
        if not lanes:
            return "No lane routes here."
        return format_html_join(
            "",
            "<div>{}{}</div>",
            (
                (
                    admin_link(lane, lane.name),
                    (
                        format_html(
                            ' <span style="color:#b26a00;">&#9888; cannot deliver: {}</span>',
                            gap,
                        )
                        if (gap := lane.delivery_gap()) is not None
                        else ""
                    ),
                )
                for lane in lanes
            ),
        )
