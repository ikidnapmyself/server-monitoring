"""
Pipeline inspection services.

Provides reusable data fetching and rendering for pipeline definitions,
used by management commands (show_pipeline, setup_instance, monitor_pipeline)
and bash scripts (cli.sh, install.sh).
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


@dataclass
class PipelineDetail:
    """Pipeline definition details as a plain data object."""

    name: str
    description: str
    flow: list[str]
    checkers: list[str]
    intelligence: str | None
    notify_drivers: list[str]
    channels: list[dict] = field(default_factory=list)
    created_at: str = ""
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


class PipelineInspector:
    """Fetch and render pipeline definition details."""

    @staticmethod
    def _extract_detail(defn: PipelineDefinition) -> PipelineDetail:
        """Extract a PipelineDetail from a PipelineDefinition instance."""
        nodes = defn.get_nodes()

        flow = [n.get("id", n.get("type", "?")) for n in nodes]

        checkers: list[str] = []
        intelligence: str | None = None
        notify_drivers: list[str] = []

        # Pipeline nodes are single-linear chains — one node per type.
        # If multiple nodes of the same type exist, last one wins.
        for node in nodes:
            node_type = node.get("type", "?")
            node_config = node.get("config", {})
            if node_type == "context":
                checkers = node_config.get("checker_names", [])
            elif node_type == "intelligence":
                intelligence = node_config.get("provider")
            elif node_type == "notify":
                notify_drivers = node_config.get("drivers", [])

        # Wizard-created channels are global (not scoped per-pipeline)
        # since PipelineDefinition has no FK to NotificationChannel.
        # Filter by driver to show only channels relevant to this pipeline.
        wizard_channels = NotificationChannel.objects.filter(
            description__startswith="[setup_wizard]", is_active=True
        ).order_by("name")
        channels = [
            {"name": ch.name, "driver": ch.driver}
            for ch in wizard_channels
            if ch.driver in notify_drivers
        ]

        return PipelineDetail(
            name=defn.name,
            description=defn.description,
            flow=flow,
            checkers=checkers,
            intelligence=intelligence,
            notify_drivers=notify_drivers,
            channels=channels,
            created_at=defn.created_at.strftime("%Y-%m-%d %H:%M"),
            is_active=defn.is_active,
        )

    @staticmethod
    def list_all(active_only: bool = True) -> list[PipelineDetail]:
        """Return details for all pipeline definitions."""
        qs = PipelineDefinition.objects.all()
        if active_only:
            qs = qs.filter(is_active=True)
        qs = qs.order_by("name")
        return [PipelineInspector._extract_detail(defn) for defn in qs]

    @staticmethod
    def get_by_name(name: str) -> PipelineDetail | None:
        """Return details for a specific pipeline by name, or None."""
        try:
            defn = PipelineDefinition.objects.get(name=name)
        except PipelineDefinition.DoesNotExist:
            return None
        return PipelineInspector._extract_detail(defn)

    @staticmethod
    def render_text(detail: PipelineDetail, stdout: Any) -> None:
        """Write styled pipeline details to a Django command stdout."""
        from django.core.management.color import color_style

        style = color_style()

        stdout.write(style.HTTP_INFO(f'\n--- Pipeline: "{detail.name}" ---'))

        if not detail.is_active:
            stdout.write(style.WARNING("  (inactive)"))

        if detail.flow:
            chain = " \u2192 ".join(detail.flow)
            stdout.write(f"  Flow: {chain}")

        if detail.checkers:
            stdout.write(f"  Checkers: {', '.join(detail.checkers)}")

        if detail.intelligence:
            stdout.write(f"  Intelligence: {detail.intelligence}")

        if detail.notify_drivers:
            stdout.write(f"  Notify drivers: {', '.join(detail.notify_drivers)}")

        if detail.channels:
            stdout.write("  Channels:")
            for ch in detail.channels:
                stdout.write(f"    - {ch['name']} ({ch['driver']})")

        if detail.created_at:
            stdout.write(f"  Created: {detail.created_at}")
