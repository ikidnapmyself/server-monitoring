---
title: "Pipeline Inspector Implementation Plan"
parent: Plans
nav_order: 79739698
---
# Pipeline Inspector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract pipeline display logic into a reusable service layer and expose it via a new `show_pipeline` management command, then integrate into all CLI consumers.

**Architecture:** `PipelineInspector` service in `apps/orchestration/services.py` with `PipelineDetail` dataclass. A thin `show_pipeline` management command wraps it for CLI/bash access. Existing commands (`setup_instance`, `monitor_pipeline`) and bash scripts (`cli.sh`, `install.sh`) consume the service.

**Tech Stack:** Django ORM, Python dataclasses, Django management commands, bash

---

### Task 1: PipelineDetail dataclass and PipelineInspector.list_all

**Files:**
- Create: `apps/orchestration/services.py`
- Create: `apps/orchestration/_tests/test_services.py`

**Step 1: Write the failing tests**

```python
"""Tests for pipeline inspector service."""

from dataclasses import asdict
from io import StringIO

from django.core.management.base import BaseCommand
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
            name="beta", config={"version": "1.0", "nodes": []},
        )
        PipelineDefinition.objects.create(
            name="alpha", config={"version": "1.0", "nodes": []},
        )
        result = PipelineInspector.list_all()
        assert [d.name for d in result] == ["alpha", "beta"]
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_services.py -v`
Expected: ImportError — `apps.orchestration.services` does not exist

**Step 3: Write the implementation**

```python
"""
Pipeline inspection services.

Provides reusable data fetching and rendering for pipeline definitions,
used by management commands (show_pipeline, setup_instance, monitor_pipeline)
and bash scripts (cli.sh, install.sh).
"""

from dataclasses import asdict, dataclass, field

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

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict."""
        return asdict(self)


class PipelineInspector:
    """Fetch and render pipeline definition details."""

    @staticmethod
    def _extract_detail(defn: PipelineDefinition) -> PipelineDetail:
        """Extract a PipelineDetail from a PipelineDefinition instance."""
        nodes = defn.get_nodes()

        flow = [n.get("id", n.get("type", "?")) for n in nodes]

        checkers = []
        intelligence = None
        notify_drivers = []

        for node in nodes:
            node_type = node.get("type", "?")
            node_config = node.get("config", {})
            if node_type == "context":
                checkers = node_config.get("checker_names", [])
            elif node_type == "intelligence":
                intelligence = node_config.get("provider")
            elif node_type == "notify":
                notify_drivers = node_config.get("drivers", [])

        # Linked wizard-created channels
        wizard_channels = NotificationChannel.objects.filter(
            description__startswith="[setup_wizard]", is_active=True
        )
        channels = [
            {"name": ch.name, "driver": ch.driver} for ch in wizard_channels
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
    def render_text(detail: PipelineDetail, stdout) -> None:
        """Write styled pipeline details to a Django command stdout."""
        from django.core.management.base import OutputWrapper
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_services.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add apps/orchestration/services.py apps/orchestration/_tests/test_services.py
git commit -m "feat: add PipelineInspector service with list_all and get_by_name"
```

---

### Task 2: PipelineInspector.get_by_name and render_text tests

**Files:**
- Modify: `apps/orchestration/_tests/test_services.py`

**Step 1: Add tests for get_by_name and render_text**

Append to `test_services.py`:

```python
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
```

**Step 2: Run tests**

Run: `uv run pytest apps/orchestration/_tests/test_services.py -v`
Expected: All pass (implementation already covers these)

**Step 3: Check coverage**

Run: `uv run coverage run -m pytest apps/orchestration/_tests/test_services.py -q && uv run coverage report --include="apps/orchestration/services.py" --show-missing`
Expected: 100% coverage. If not, add tests for uncovered branches.

**Step 4: Commit**

```bash
git add apps/orchestration/_tests/test_services.py
git commit -m "test: add get_by_name and render_text coverage for PipelineInspector"
```

---

### Task 3: show_pipeline management command

**Files:**
- Create: `apps/orchestration/management/commands/show_pipeline.py`
- Create: `apps/orchestration/_tests/test_show_pipeline.py`

**Step 1: Write the failing tests**

```python
"""Tests for the show_pipeline management command."""

import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class ShowPipelineListTests(TestCase):
    """Tests for show_pipeline list mode (default)."""

    def test_empty_state_shows_message(self):
        out = StringIO()
        call_command("show_pipeline", stdout=out)
        assert "No pipeline definitions found" in out.getvalue()

    def test_lists_active_pipelines(self):
        PipelineDefinition.objects.create(
            name="full",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ingest", "type": "ingest", "config": {}},
                    {"id": "notify", "type": "notify", "config": {"drivers": ["slack"]}},
                ],
            },
        )
        out = StringIO()
        call_command("show_pipeline", stdout=out)
        output = out.getvalue()
        assert "full" in output
        assert "ingest" in output

    def test_excludes_inactive_by_default(self):
        PipelineDefinition.objects.create(
            name="old",
            config={"version": "1.0", "nodes": []},
            is_active=False,
        )
        out = StringIO()
        call_command("show_pipeline", stdout=out)
        assert "No pipeline definitions found" in out.getvalue()

    def test_all_flag_includes_inactive(self):
        PipelineDefinition.objects.create(
            name="old",
            config={"version": "1.0", "nodes": []},
            is_active=False,
        )
        out = StringIO()
        call_command("show_pipeline", all=True, stdout=out)
        output = out.getvalue()
        assert "old" in output
        assert "(inactive)" in output


class ShowPipelineNameTests(TestCase):
    """Tests for show_pipeline --name mode."""

    def test_shows_specific_pipeline(self):
        PipelineDefinition.objects.create(
            name="full",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ingest", "type": "ingest", "config": {}},
                ],
            },
        )
        out = StringIO()
        call_command("show_pipeline", name="full", stdout=out)
        assert "full" in out.getvalue()

    def test_not_found_shows_error(self):
        out = StringIO()
        call_command("show_pipeline", name="nonexistent", stdout=out)
        assert "not found" in out.getvalue().lower()


class ShowPipelineJsonTests(TestCase):
    """Tests for show_pipeline --json mode."""

    def test_json_list_output(self):
        PipelineDefinition.objects.create(
            name="direct",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "notify", "type": "notify", "config": {"drivers": ["slack"]}},
                ],
            },
        )
        out = StringIO()
        call_command("show_pipeline", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "direct"

    def test_json_name_output(self):
        PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
        )
        out = StringIO()
        call_command("show_pipeline", name="full", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        assert isinstance(data, dict)
        assert data["name"] == "full"

    def test_json_not_found(self):
        out = StringIO()
        call_command("show_pipeline", name="nope", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        assert data["error"] == "not_found"

    def test_json_empty_list(self):
        out = StringIO()
        call_command("show_pipeline", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        assert data == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_show_pipeline.py -v`
Expected: ModuleNotFoundError for `show_pipeline` command

**Step 3: Write the implementation**

```python
"""
Display pipeline definitions.

Usage:
    python manage.py show_pipeline              # list all active
    python manage.py show_pipeline --all        # include inactive
    python manage.py show_pipeline --name X     # specific pipeline
    python manage.py show_pipeline --json       # JSON output
"""

import json

from django.core.management.base import BaseCommand

from apps.orchestration.services import PipelineInspector


class Command(BaseCommand):
    help = "Display pipeline definitions with full details."

    def add_arguments(self, parser):
        parser.add_argument(
            "--name",
            type=str,
            help="Show a specific pipeline by name.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="show_all",
            help="Include inactive pipelines.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output as JSON.",
        )

    def handle(self, *args, **options):
        name = options.get("name")
        show_all = options.get("show_all", False)
        json_output = options.get("json_output", False)

        if name:
            self._show_single(name, json_output)
        else:
            self._show_list(show_all, json_output)

    def _show_single(self, name, json_output):
        detail = PipelineInspector.get_by_name(name)
        if detail is None:
            if json_output:
                self.stdout.write(json.dumps({"error": "not_found", "name": name}))
            else:
                self.stdout.write(self.style.ERROR(f'Pipeline "{name}" not found.'))
            return
        if json_output:
            self.stdout.write(json.dumps(detail.to_dict(), indent=2))
        else:
            PipelineInspector.render_text(detail, self.stdout)

    def _show_list(self, show_all, json_output):
        details = PipelineInspector.list_all(active_only=not show_all)
        if json_output:
            self.stdout.write(json.dumps([d.to_dict() for d in details], indent=2))
            return
        if not details:
            self.stdout.write(self.style.WARNING("No pipeline definitions found."))
            return
        for detail in details:
            PipelineInspector.render_text(detail, self.stdout)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_show_pipeline.py -v`
Expected: All pass

**Step 5: Check coverage**

Run: `uv run coverage run -m pytest apps/orchestration/_tests/test_show_pipeline.py -q && uv run coverage report --include="apps/orchestration/management/commands/show_pipeline.py" --show-missing`
Expected: 100%

**Step 6: Commit**

```bash
git add apps/orchestration/management/commands/show_pipeline.py apps/orchestration/_tests/test_show_pipeline.py
git commit -m "feat: add show_pipeline management command with JSON support"
```

---

### Task 4: Refactor setup_instance to use PipelineInspector

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py:602-645`
- Modify: `apps/orchestration/_tests/test_setup_instance.py:687-810`

**Step 1: Replace `_show_existing_details` body**

In `setup_instance.py`, replace the `_show_existing_details` method body with a delegation to `PipelineInspector`:

```python
    def _show_existing_details(self, existing):
        """
        Display details of an existing wizard-created pipeline.

        Args:
            existing: PipelineDefinition instance.
        """
        from apps.orchestration.services import PipelineInspector

        detail = PipelineInspector.get_by_name(existing.name)
        if detail:
            PipelineInspector.render_text(detail, self.stdout)
```

This replaces the entire ~40-line method body with 4 lines.

**Step 2: Run existing tests to verify nothing broke**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v`
Expected: All 102 tests pass

**Step 3: Check coverage on setup_instance**

Run: `uv run coverage run -m pytest apps/orchestration/_tests/test_setup_instance.py -q && uv run coverage report --include="apps/orchestration/management/commands/setup_instance.py" --show-missing`
Expected: 100%. The `ShowExistingDetailsTests` class now exercises the delegation path. The detailed rendering is covered in `test_services.py`.

**Step 4: Remove the now-redundant `ShowExistingDetailsTests` from test_setup_instance.py**

The detailed rendering tests now live in `test_services.py`. Replace the `ShowExistingDetailsTests` class in `test_setup_instance.py` with a minimal test that verifies the delegation:

```python
class ShowExistingDetailsTests(TestCase):
    """Tests for _show_existing_details delegation to PipelineInspector."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_delegates_to_pipeline_inspector(self):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "check_health",
                        "type": "context",
                        "config": {"checker_names": ["cpu", "memory"]},
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
        self.cmd._show_existing_details(defn)
        output = self.cmd.stdout.getvalue()
        # Verify the service rendered the details
        assert "full" in output
        assert "check_health" in output

    def test_handles_missing_pipeline_gracefully(self):
        """get_by_name returns None for a name that doesn't match."""
        defn = PipelineDefinition.objects.create(
            name="test",
            config={"version": "1.0", "nodes": []},
            created_by="setup_instance",
        )
        # Delete so get_by_name returns None
        defn.delete()
        self.cmd._show_existing_details(defn)
        # Should not crash — just no output
        output = self.cmd.stdout.getvalue()
        assert "test" not in output
```

**Step 5: Run all tests**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py apps/orchestration/_tests/test_services.py -v`
Expected: All pass

**Step 6: Check coverage on both files**

Run: `uv run coverage run -m pytest apps/orchestration/_tests/ -q && uv run coverage report --include="apps/orchestration/services.py,apps/orchestration/management/commands/setup_instance.py" --show-missing`
Expected: 100% on both

**Step 7: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "refactor: delegate setup_instance display to PipelineInspector service"
```

---

### Task 5: Integrate into monitor_pipeline

**Files:**
- Modify: `apps/orchestration/management/commands/monitor_pipeline.py:70-97`

**Step 1: Add pipeline definition display to `show_run_details`**

In `monitor_pipeline.py`, after line 77 (`self.stdout.write(self.style.HTTP_INFO(...))`), add:

```python
        # Show linked pipeline definition if available
        from apps.orchestration.services import PipelineInspector

        # Try to find a pipeline definition matching this run's source or trace
        details = PipelineInspector.list_all()
        if details:
            for detail in details:
                PipelineInspector.render_text(detail, self.stdout)
            self.stdout.write("")
```

Note: `PipelineRun` doesn't have a FK to `PipelineDefinition`, so we show all active definitions as context. This is the simplest correct approach given the current schema.

**Step 2: Run full test suite**

Run: `uv run pytest apps/orchestration/_tests/ -v`
Expected: All pass

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/monitor_pipeline.py
git commit -m "feat: show pipeline definitions inline in monitor_pipeline detail view"
```

---

### Task 6: Integrate into cli.sh

**Files:**
- Modify: `bin/cli.sh:493-512`

**Step 1: Add "View pipelines" option to pipeline_menu**

Replace the `pipeline_menu` function:

```bash
pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ Pipeline Orchestration ═══${NC}"
    echo ""

    local options=(
        "show_pipeline - View pipeline definitions"
        "run_pipeline - Execute a pipeline"
        "monitor_pipeline - Monitor pipeline execution"
        "Back to main menu"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) show_pipeline_menu ;;
            2) run_pipeline_menu ;;
            3) monitor_pipeline_menu ;;
            4) return ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}
```

**Step 2: Add the `show_pipeline_menu` function**

Add after the `pipeline_menu` function:

```bash
show_pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ show_pipeline ═══${NC}"
    echo ""
    echo "View pipeline definitions and their configuration"
    echo ""

    local options=(
        "Show all active pipelines"
        "Show all pipelines (including inactive)"
        "Show specific pipeline"
        "Show as JSON"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py show_pipeline"
                ;;
            2)
                confirm_and_run "uv run python manage.py show_pipeline --all"
                ;;
            3)
                read -p "Enter pipeline name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py show_pipeline --name $pipeline_name"
                else
                    echo -e "${RED}Pipeline name required${NC}"
                fi
                ;;
            4)
                confirm_and_run "uv run python manage.py show_pipeline --json"
                ;;
            5)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}
```

**Step 3: Commit**

```bash
git add bin/cli.sh
git commit -m "feat: add show_pipeline menu option to cli.sh"
```

---

### Task 7: Integrate into install.sh

**Files:**
- Modify: `bin/install.sh:344-359`

**Step 1: Add pipeline summary after setup**

After the cron setup prompt (line 350) and before the aliases prompt (line 352), add:

```bash
# Show existing pipeline definitions if any
echo ""
PIPELINE_COUNT=$(uv run python manage.py show_pipeline --json 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [ "$PIPELINE_COUNT" != "0" ] && [ "$PIPELINE_COUNT" != "" ]; then
    info "Found $PIPELINE_COUNT configured pipeline(s):"
    uv run python manage.py show_pipeline
    echo ""
else
    info "No pipelines configured yet. Run the setup wizard to create one:"
    echo "  uv run python manage.py setup_instance"
    echo ""
fi
```

**Step 2: Commit**

```bash
git add bin/install.sh
git commit -m "feat: show pipeline summary in install.sh post-setup"
```

---

### Task 8: Final verification and cleanup

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Check coverage on all new/modified files**

Run: `uv run coverage run -m pytest apps/orchestration/_tests/ -q && uv run coverage report --include="apps/orchestration/services.py,apps/orchestration/management/commands/show_pipeline.py,apps/orchestration/management/commands/setup_instance.py,apps/orchestration/management/commands/monitor_pipeline.py" --show-missing`
Expected: 100% on services.py, show_pipeline.py, setup_instance.py

**Step 3: Lint and format**

Run: `uv run black . && uv run ruff check . --fix`
Expected: Clean

**Step 4: Run pre-commit**

Run: `uv run pre-commit run --all-files`
Expected: All pass

**Step 5: Final commit if any formatting changes**

```bash
git add -A && git commit -m "style: format and lint"
```