# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django-based server monitoring and alerting system with a strict 4-stage orchestration pipeline: `alerts → checkers → intelligence → notify`. The orchestrator controls all stage transitions—stages never call downstream stages directly.

## Essential Commands

```bash
# Install dependencies
uv sync --extra dev

# Run tests
uv run pytest                              # All tests
uv run pytest apps/checkers/tests/         # Single app
uv run pytest apps/checkers/tests/test_cpu.py -v  # Single file

# Code quality
uv run black .                             # Format
uv run ruff check . --fix                  # Lint + fix imports
uv run mypy .                              # Type check (optional)

# Pre-commit hooks
uv run pre-commit install
uv run pre-commit run --all-files

# Django
uv run python manage.py migrate
uv run python manage.py runserver
uv run python manage.py check              # Django system checks

# Health checks
uv run python manage.py check_health       # Run all checks
uv run python manage.py check_health --list
uv run python manage.py run_check cpu      # Single checker

# Pipeline testing
uv run python manage.py run_pipeline --sample
uv run python manage.py run_pipeline --sample --dry-run
```

## Architecture

### Pipeline Flow
```
alerts.ingest() → checkers.run() → intelligence.analyze() → notify.dispatch()
```

Each stage emits monitoring signals (`pipeline.stage.started/succeeded/failed`) with correlation IDs (`trace_id`, `run_id`).

### App Structure

All apps under `apps/` follow this layout:
- `views/` — Package (not monolithic `views.py`), organized by endpoint
- `tests/` — Package mirroring source structure (e.g., `tests/views/test_webhook.py`)
- `agents.md` — App-specific AI agent guidance
- `admin.py` — Extensive admin for operations

### Core Apps

| App | Purpose | Key Models |
|-----|---------|------------|
| `alerts` | Webhook ingestion (8 drivers) | Alert, Incident, AlertHistory |
| `checkers` | Health checks (CPU, memory, disk, network, process) | CheckRun |
| `intelligence` | AI analysis via provider pattern | Uses StageExecution |
| `notify` | Notification delivery (Email, Slack, PagerDuty, Generic) | NotificationChannel |
| `orchestration` | Pipeline state machine, retry logic | PipelineRun, StageExecution, PipelineDefinition |

### Key Patterns

- **Driver/Provider Pattern**: All integrations inherit from abstract base classes (e.g., `BaseDriver`, `BaseChecker`, `BaseProvider`)
- **DTOs**: Normalized data objects between stages (ParsedPayload, CheckResult, AnalysisResult)
- **Correlation IDs**: Every pipeline run has `trace_id` and `run_id` for tracing
- **Skip Controls**: `CHECKERS_SKIP_ALL=1` or `CHECKERS_SKIP=cpu,memory` to disable stages

## Code Conventions

- **Imports**: Always absolute — `from apps.alerts.models import Incident`
- **Line length**: 100 characters (Black, Ruff)
- **Settings**: `config/settings.py`
- **Env vars**: Copy `.env.sample` to `.env`

## Key Documentation

- `agents.md` — AI agent roles and pipeline contracts (read this for any significant work)
- `apps/<app>/README.md` — App-specific documentation
- `apps/<app>/agents.md` — App-specific AI guidance
- `docs/orchestration-pipelines.md` — Pipeline architecture details
