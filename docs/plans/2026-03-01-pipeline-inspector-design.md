---
title: "Pipeline Inspector — Design"
parent: Plans
nav_order: 79739698
---
# Pipeline Inspector — Design

## Problem

`setup_instance._show_existing_details` renders pipeline definition details but is locked inside a
management command class. The same data is needed across multiple consumers: other management
commands (`monitor_pipeline`, `show_pipeline`), bash scripts (`cli.sh`, `install.sh`), and
potentially future tooling.

## Decision

**Approach A: Service layer + management command.** Extract a `PipelineInspector` service into
`apps/orchestration/services.py` (flat file, consistent with `apps/alerts/services.py` and
`apps/notify/services.py`). Wrap it in a new `show_pipeline` management command. Bash scripts
call the command via `uv run python manage.py show_pipeline`.

## Architecture

```
                    ┌─────────────────────────┐
                    │  PipelineInspector       │
                    │  (services.py)           │
                    │                          │
                    │  list_all() → [Detail]   │
                    │  get_by_name() → Detail  │
                    │  render_text()           │
                    └────────┬────────────────┘
                             │
             ┌───────────────┼───────────────────┐
             │               │                   │
     ┌───────▼──────┐ ┌─────▼───────┐  ┌────────▼────────┐
     │ show_pipeline │ │setup_instance│  │monitor_pipeline │
     │ (command)     │ │(command)     │  │(command)        │
     └───────┬──────┘ └─────────────┘  └─────────────────┘
             │
     ┌───────▼──────┐
     │  cli.sh      │
     │  install.sh  │
     │  (bash)      │
     └──────────────┘
```

### Data flow: Django → Python → uv → CLI

1. **Django ORM** — `PipelineInspector` queries `PipelineDefinition` and `NotificationChannel`
2. **Python dataclass** — Results returned as `PipelineDetail` (pure data, no Django dependencies)
3. **Management command** — `show_pipeline` serializes to text or JSON via `--json` flag
4. **uv** — Bash scripts call `uv run python manage.py show_pipeline [--json]`
5. **CLI** — `cli.sh` pipes output directly; `install.sh` can parse JSON with `jq` if needed

## Service Layer

**File**: `apps/orchestration/services.py`

```python
@dataclass
class PipelineDetail:
    name: str
    description: str
    flow: list[str]            # ["check_health", "analyze_incident", "notify_channels"]
    checkers: list[str]        # ["cpu", "memory"]
    intelligence: str | None   # "openai" or None
    notify_drivers: list[str]  # ["slack", "email"]
    channels: list[dict]       # [{"name": "ops-slack", "driver": "slack"}]
    created_at: str            # "2026-02-28 14:30"
    is_active: bool

    def to_dict(self) -> dict  # JSON-serializable dict

class PipelineInspector:
    @staticmethod
    def list_all(active_only=True) -> list[PipelineDetail]

    @staticmethod
    def get_by_name(name: str) -> PipelineDetail | None

    @staticmethod
    def render_text(detail: PipelineDetail, stdout) -> None
```

- `list_all` / `get_by_name` return data — no rendering, no side effects
- `render_text` takes a `PipelineDetail` and a Django `stdout`, writes styled output
- `to_dict` returns a plain dict for JSON serialization

## Management Command

**File**: `apps/orchestration/management/commands/show_pipeline.py`

```
Usage:
  manage.py show_pipeline              # list all active pipelines (full details)
  manage.py show_pipeline --all        # include inactive pipelines
  manage.py show_pipeline --name X     # show specific pipeline by name
  manage.py show_pipeline --json       # JSON output for bash/scripting
```

Thin wrapper: calls `PipelineInspector`, renders text or JSON.

## Consumers

### setup_instance.py

Replace `_show_existing_details` body:

```python
detail = PipelineInspector.get_by_name(existing.name)
if detail:
    PipelineInspector.render_text(detail, self.stdout)
```

### monitor_pipeline.py

In detail mode (`--run-id`), show the linked PipelineDefinition details inline above the run
info by calling `render_text()`.

### bin/cli.sh

Add a "View pipeline(s)" option under the Pipeline Orchestration menu:

```bash
uv run python manage.py show_pipeline
```

### bin/install.sh

At the end of installation, if wizard-created pipelines exist, show a summary:

```bash
uv run python manage.py show_pipeline --json
```

### list_notify_drivers.py

No change — lists drivers, not pipelines.

## Testing

- `_tests/test_services.py` — Unit tests for `PipelineInspector` (list_all, get_by_name,
  render_text, to_dict). 100% branch coverage required.
- `_tests/test_show_pipeline.py` — Command integration tests (text output, JSON output,
  --name flag, --all flag, empty state).
- `_tests/test_setup_instance.py` — Update existing tests for the refactored
  `_show_existing_details`.