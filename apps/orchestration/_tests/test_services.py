"""Tests for pipeline inspector service."""

from io import StringIO

from django.test import TestCase

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.services import PipelineDetail, PipelineInspector


class PipelineDetailTests(TestCase):
    """Tests for PipelineDetail dataclass."""

    def test_to_dict_returns_all_fields(self):
        detail = PipelineDetail(
            name="full",
            description="Full pipeline",
            flow=["ingest", "check", "analyze", "notify"],
            checkers=["cpu", "memory"],
            intelligence="openai",
            notify_drivers=["slack"],
            channels=[{"name": "ops-slack", "driver": "slack"}],
            created_at="2026-02-28 14:30",
            is_active=True,
        )
        d = detail.to_dict()
        assert d["name"] == "full"
        assert d["flow"] == ["ingest", "check", "analyze", "notify"]
        assert d["intelligence"] == "openai"
        assert d["is_active"] is True

    def test_to_dict_with_none_intelligence(self):
        detail = PipelineDetail(
            name="direct",
            description="Direct",
            flow=["ingest", "notify"],
            checkers=[],
            intelligence=None,
            notify_drivers=["slack"],
            channels=[],
            created_at="2026-02-28 14:30",
            is_active=True,
        )
        d = detail.to_dict()
        assert d["intelligence"] is None
        assert d["checkers"] == []


class ListAllTests(TestCase):
    """Tests for PipelineInspector.list_all."""

    def test_returns_empty_list_when_no_pipelines(self):
        result = PipelineInspector.list_all()
        assert result == []

    def test_returns_active_pipelines(self):
        PipelineDefinition.objects.create(
            name="full",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "check_health",
                        "type": "context",
                        "config": {"checker_names": ["cpu", "memory"]},
                        "next": "analyze_incident",
                    },
                    {
                        "id": "analyze_incident",
                        "type": "intelligence",
                        "config": {"provider": "openai"},
                        "next": "notify_channels",
                    },
                    {
                        "id": "notify_channels",
                        "type": "notify",
                        "config": {"drivers": ["slack"]},
                    },
                ],
            },
            created_by="setup_instance",
        )
        result = PipelineInspector.list_all()
        assert len(result) == 1
        detail = result[0]
        assert detail.name == "full"
        assert detail.flow == ["check_health", "analyze_incident", "notify_channels"]
        assert detail.checkers == ["cpu", "memory"]
        assert detail.intelligence == "openai"
        assert detail.notify_drivers == ["slack"]
        assert detail.is_active is True

    def test_excludes_inactive_by_default(self):
        PipelineDefinition.objects.create(
            name="old",
            config={"version": "1.0", "nodes": []},
            is_active=False,
        )
        result = PipelineInspector.list_all()
        assert result == []

    def test_includes_inactive_when_requested(self):
        PipelineDefinition.objects.create(
            name="old",
            config={"version": "1.0", "nodes": []},
            is_active=False,
        )
        result = PipelineInspector.list_all(active_only=False)
        assert len(result) == 1
        assert result[0].is_active is False

    def test_includes_linked_channels(self):
        PipelineDefinition.objects.create(
            name="direct",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "notify", "type": "notify", "config": {"drivers": ["slack"]}},
                ],
            },
            created_by="setup_instance",
        )
        NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={},
            description="[setup_wizard] slack channel",
        )
        result = PipelineInspector.list_all()
        assert len(result[0].channels) == 1
        assert result[0].channels[0]["name"] == "ops-slack"
        assert result[0].channels[0]["driver"] == "slack"

    def test_no_intelligence_when_absent(self):
        PipelineDefinition.objects.create(
            name="direct",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ingest", "type": "ingest", "config": {}},
                    {"id": "notify", "type": "notify", "config": {"drivers": ["slack"]}},
                ],
            },
        )
        result = PipelineInspector.list_all()
        assert result[0].intelligence is None

    def test_empty_checkers_when_no_context_node(self):
        PipelineDefinition.objects.create(
            name="direct",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "notify", "type": "notify", "config": {"drivers": ["slack"]}},
                ],
            },
        )
        result = PipelineInspector.list_all()
        assert result[0].checkers == []

    def test_multiple_pipelines_sorted_by_name(self):
        PipelineDefinition.objects.create(
            name="beta",
            config={"version": "1.0", "nodes": []},
        )
        PipelineDefinition.objects.create(
            name="alpha",
            config={"version": "1.0", "nodes": []},
        )
        result = PipelineInspector.list_all()
        assert [d.name for d in result] == ["alpha", "beta"]


class GetByNameTests(TestCase):
    """Tests for PipelineInspector.get_by_name."""

    def test_returns_detail_for_existing_pipeline(self):
        PipelineDefinition.objects.create(
            name="full",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "check_health",
                        "type": "context",
                        "config": {"checker_names": ["cpu"]},
                    },
                ],
            },
        )
        detail = PipelineInspector.get_by_name("full")
        assert detail is not None
        assert detail.name == "full"
        assert detail.checkers == ["cpu"]

    def test_returns_none_for_missing_pipeline(self):
        result = PipelineInspector.get_by_name("nonexistent")
        assert result is None

    def test_returns_inactive_pipeline(self):
        PipelineDefinition.objects.create(
            name="old",
            config={"version": "1.0", "nodes": []},
            is_active=False,
        )
        detail = PipelineInspector.get_by_name("old")
        assert detail is not None
        assert detail.is_active is False


class RenderTextTests(TestCase):
    """Tests for PipelineInspector.render_text."""

    def test_renders_full_pipeline(self):
        detail = PipelineDetail(
            name="full",
            description="Full pipeline",
            flow=["ingest", "check", "analyze", "notify"],
            checkers=["cpu", "memory"],
            intelligence="openai",
            notify_drivers=["slack", "email"],
            channels=[{"name": "ops-slack", "driver": "slack"}],
            created_at="2026-02-28 14:30",
            is_active=True,
        )
        stdout = StringIO()
        PipelineInspector.render_text(detail, stdout)
        output = stdout.getvalue()
        assert '"full"' in output
        assert "ingest" in output
        assert "cpu, memory" in output
        assert "openai" in output
        assert "slack, email" in output
        assert "ops-slack (slack)" in output
        assert "2026-02-28 14:30" in output

    def test_renders_minimal_pipeline(self):
        detail = PipelineDetail(
            name="empty",
            description="",
            flow=[],
            checkers=[],
            intelligence=None,
            notify_drivers=[],
            channels=[],
            created_at="",
            is_active=True,
        )
        stdout = StringIO()
        PipelineInspector.render_text(detail, stdout)
        output = stdout.getvalue()
        assert '"empty"' in output
        assert "Flow:" not in output
        assert "Checkers:" not in output
        assert "Intelligence:" not in output

    def test_renders_inactive_marker(self):
        detail = PipelineDetail(
            name="old",
            description="",
            flow=["notify"],
            checkers=[],
            intelligence=None,
            notify_drivers=[],
            channels=[],
            created_at="2026-01-01 00:00",
            is_active=False,
        )
        stdout = StringIO()
        PipelineInspector.render_text(detail, stdout)
        output = stdout.getvalue()
        assert "(inactive)" in output
